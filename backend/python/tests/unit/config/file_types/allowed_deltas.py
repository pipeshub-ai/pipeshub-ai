"""Decisions that may differ from the snapshot pinned on main, each with the reason it changed.

Keyed by ``gen_pinned_decisions.case_key`` of the row's (extension, mime, name, record type); the
value maps a column name to the new decision. A change that alters behaviour adds its rows here
and nowhere else.
"""
from __future__ import annotations

from typing import Any

ALLOWED_DELTAS: dict[str, dict[str, Any]] = {}
ALLOWED_GLOBAL_DELTAS: dict[str, Any] = {}
