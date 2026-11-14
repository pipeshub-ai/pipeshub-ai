"""Decisions that differ from the snapshot pinned on main, each with the reviewed reason it changed.

``allowed_deltas.json`` is written by ``delta_rules`` from a replay and holds, per changed row
(keyed by ``gen_pinned_decisions.case_key``), the new value of each changed column and the reason
codes of ``delta_rules.REASONS``. A change that alters behaviour adds its rows there and nowhere
else, and ``test_every_allowed_delta_follows_its_reviewed_rule`` re-checks each one.
"""
from __future__ import annotations

import json
from typing import Any

from tests.unit.config.file_types.delta_rules import ALLOWED_DELTAS_FILE

_DATA: dict[str, Any] = json.loads(ALLOWED_DELTAS_FILE.read_text())

ALLOWED_DELTAS: dict[str, dict[str, Any]] = {
    key: {col: value for col, value in entry.items() if col != "why"}
    for key, entry in _DATA["rows"].items()
}
ALLOWED_DELTA_REASONS: dict[str, list[str]] = {key: entry["why"] for key, entry in _DATA["rows"].items()}
ALLOWED_MIME_DELTAS: dict[str, dict[str, Any]] = _DATA["mimes"]
ALLOWED_GLOBAL_DELTAS: dict[str, Any] = _DATA["globals"]
