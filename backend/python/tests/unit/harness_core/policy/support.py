"""The shared policy vectors (``contracts/harness/vectors/policy-resolve.json``, ``approvals.json`` and QA-2's
``contracts/harness/vectors/stricter-only.json``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VECTORS_DIR = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors"
POLICY_VECTORS: dict[str, Any] = json.loads((VECTORS_DIR / "policy-resolve.json").read_text(encoding="utf-8"))
APPROVAL_VECTORS: dict[str, Any] = json.loads((VECTORS_DIR / "approvals.json").read_text(encoding="utf-8"))
# With the ladders an edition's overlay adds for the dimensions it adds (``contracts/harness/ee/vectors/``, if present).
OVERLAY_VECTORS_DIR = VECTORS_DIR.parent / "ee" / "vectors"


def _with_overlay_ladders(vectors: dict[str, Any]) -> dict[str, Any]:
    overlay = OVERLAY_VECTORS_DIR / "stricter-only.json"
    if not overlay.is_file():
        return vectors
    ladders: list[dict[str, Any]] = json.loads(overlay.read_text(encoding="utf-8"))["ladders"]
    known = {ladder["path"] for ladder in vectors["ladders"]}
    repeated = [ladder["path"] for ladder in ladders if ladder["path"] in known]
    if repeated:
        raise ValueError(f"{overlay}: ladders for {repeated} are already defined")
    return {**vectors, "ladders": [*vectors["ladders"], *ladders]}


STRICTER_ONLY: dict[str, Any] = _with_overlay_ladders(
    json.loads((VECTORS_DIR / "stricter-only.json").read_text(encoding="utf-8"))
)
