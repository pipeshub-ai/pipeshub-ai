"""Declared differences from the recorded literal walks, for worlds that are not N-neutral.

Comparing names as a file share does (``\\`` separates folders; case and
Unicode form do not count) changes some recorded walks on purpose. Each such
world is listed in ``fixtures/network_share_walk_deltas.json`` with why it
differs and the walk now expected. A world that is N-neutral can never be
listed: it must replay exactly.

    EXPECTED_DELTAS[world] = {"deltas": ["case", ...], "walk": {...same shape as the golden walk...}}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Why a recorded walk may change.
DELTA_KINDS = frozenset({
    "case",  # a value and a path differ only in case
    "backslash",  # a value separates folders with `\`
    "unicode_form",  # a value is typed in another Unicode form (NFD) than the share's names
    "equal_values",  # two values name the same folder once compared as a share does, so it is walked once
})

_DECLARED = Path(__file__).parent / "fixtures" / "network_share_walk_deltas.json"

EXPECTED_DELTAS: dict[int, dict[str, Any]] = {
    entry["world"]: {"deltas": entry["deltas"], "walk": entry["walk"]}
    for entry in json.loads(_DECLARED.read_text(encoding="utf-8"))
}
