"""Capability probe: what the connected PostgreSQL offers, checked against a data table of floors.

The table is ``backend/db/capabilities.yaml``. The runtime ships the same table built in (``DEFAULT_TABLE``), because the
service images do not carry ``backend/db``; a test keeps the two identical. ``DB_CAPABILITIES_FILE`` overrides both.
Bumping a "tested up to" value is a data change, not code.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from app.core.db.errors import CapabilityError
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)
DOWNGRADES = METRICS_BACKEND.counter("pipeshub_capability_downgrade_total", "Capabilities downgraded in non-strict mode", ["from", "to"])

DEFAULT_TABLE: dict[str, Any] = {
    "postgres": {"floor": 17, "tested_up_to": 18},
    "extensions": {
        "vector": {
            "required": "on_request",
            "floor": "0.8.0",
            "tested_up_to": "0.8.7",
            # Below 0.8.4 HNSW vacuum is not safe; the flag keeps the safe mode.
            "flags": [{"below": "0.8.4", "set": {"hnsw_vacuum_safe": False}, "default": {"hnsw_vacuum_safe": True}}],
        },
        "pg_trgm": {"required": "always", "floor": "1.6", "tested_up_to": "1.6"},
        "pg_textsearch": {
            "required": "optional",
            "floor": "1.0",
            "tested_up_to": "1.5.1",
            "flags": [{"installed": True, "set": {"bm25": True}, "default": {"bm25": False}}],
        },
        "pg_search": {"required": "optional"},
        "pg_stat_statements": {"required": "optional"},
    },
    # Flags that need no extension.
    "flags": {"fused_vector": {"all_of": ["vector"]}},
}

_PROBE_SQL = """
SELECT current_setting('server_version_num')::int AS pg_num,
       (SELECT coalesce(jsonb_object_agg(name, jsonb_build_object('installed', installed_version,
                                                                  'available', default_version)), '{}'::jsonb)
          FROM pg_available_extensions
         WHERE name = ANY($1::text[])) AS ext,
       CASE WHEN pg_has_role(current_user, 'pg_read_all_settings', 'USAGE')
            THEN current_setting('shared_preload_libraries', true) END AS preload,   -- NULL when not readable
       current_setting('azure.extensions', true) AS azure_allowlist
"""


def load_table() -> dict[str, Any]:
    path = os.getenv("DB_CAPABILITIES_FILE")
    if not path:
        return DEFAULT_TABLE
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:4])


@dataclass(frozen=True, slots=True)
class Capabilities:
    pg_major: int
    # installed | available | not_allow_listed | absent
    extensions: dict[str, str]
    versions: dict[str, str]
    flags: dict[str, bool]
    warnings: tuple[str, ...] = field(default=())

    def summary(self) -> str:
        ext = ",".join(f"{k}={self.versions.get(k, v)}" for k, v in sorted(self.extensions.items()))
        flags = ",".join(f"{k}={str(v).lower()}" for k, v in sorted(self.flags.items()))
        return f"pg={self.pg_major} ext[{ext}] flags[{flags}]"


def probe_sql(table: dict[str, Any]) -> tuple[str, list[str]]:
    return _PROBE_SQL, sorted(table["extensions"])


def evaluate(row: Mapping[str, Any], table: dict[str, Any], *, strict: bool, want: tuple[str, ...] = ()) -> Capabilities:
    """Pure: probe row in, capabilities (or CapabilityError in strict mode) out."""
    import json

    pg_major = int(row["pg_num"]) // 10000
    raw = row["ext"]
    found: dict[str, dict[str, Any]] = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    preload = row["preload"]  # None: the role cannot read it
    allow = row["azure_allowlist"]
    problems: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    pg = table["postgres"]
    if pg_major < pg["floor"]:
        problems.append(("postgres", f"PostgreSQL {pg_major} is below the supported floor {pg['floor']}; upgrade the server"))
    elif pg_major > pg["tested_up_to"]:
        problems.append(("postgres", f"PostgreSQL {pg_major} is newer than the tested {pg['tested_up_to']}; run it on a tested major"))
    states: dict[str, str] = {}
    versions: dict[str, str] = {}
    flags: dict[str, bool] = {}
    for name, spec in table["extensions"].items():
        info = found.get(name)
        if info is None:
            state = "absent"
        elif info.get("installed"):
            state = "installed"
            versions[name] = info["installed"]
        elif allow is not None and name not in {a.strip() for a in allow.split(",")}:
            state = "not_allow_listed"
        elif preload is not None and name in ("pg_stat_statements", "pg_textsearch") and name not in preload:
            state = "not_allow_listed"  # needs shared_preload_libraries
        else:
            state = "available"
        states[name] = state
        req = spec.get("required", "optional")
        needed = req == "always" or (req == "on_request" and name in want)
        if state == "installed":
            if "floor" in spec and _ver(versions[name]) < _ver(spec["floor"]):
                (problems if needed else warnings).append((name, f"{name} {versions[name]} is below the floor {spec['floor']}"))
            elif "tested_up_to" in spec and _ver(versions[name]) > _ver(spec["tested_up_to"]):
                warnings.append((name, f"{name} {versions[name]} is newer than the tested {spec['tested_up_to']}"))
        elif needed:
            hint = {"available": "run the migration job (it creates extensions)", "absent": "install the extension package",
                    "not_allow_listed": "allow-list it in the server configuration"}[state]
            problems.append((name, f"required extension {name} is {state}: {hint}"))
        for rule in spec.get("flags", []):
            if "below" in rule:
                hit = state == "installed" and _ver(versions[name]) < _ver(rule["below"])
            else:
                hit = (state == "installed") == rule["installed"]
            flags.update(rule["set"] if hit else rule["default"])
    for flag, rule in table.get("flags", {}).items():
        flags[flag] = all(states.get(e) == "installed" for e in rule["all_of"])
    if problems and strict:
        raise CapabilityError("; ".join(m for _, m in problems))
    for key, msg in problems:
        DOWNGRADES.inc(key, "degraded")
        logger.warning("capability downgrade: %s", msg)
    return Capabilities(pg_major, states, versions, flags, tuple(m for _, m in warnings + problems))
