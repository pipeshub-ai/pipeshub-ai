"""The part of the governor's state other processes may read, as a load signal body.

Read-only and standalone: it reads what ``ResourceGovernor.stats()`` already returns and changes
nothing. The per-pool block (limit, waiters, the rule holding the pool down, how long) comes from
``stats()["admission"]`` when the governor provides it; without it there are no pool series, which
is "unknown" and never a zero. ``project`` is an allow-list: no org, record or connector name can
reach a reader.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.services.resource_governor.probe import PressureSample

LOAD_SIGNAL_VERSION = 1
POOLS = ("index_heavy", "index_light", "heavy_parse", "light_parse")


class BrakeReason(StrEnum):
    """Which kind of rule holds a pool below its ceiling."""

    NONE = "none"
    RAMPING = "ramping"
    MEMORY_TARGET = "memory_target"
    MEMORY_HARD = "memory_hard"
    MEMORY_SOFT = "memory_soft"
    CPU = "cpu"
    DOWNSTREAM = "downstream"
    FROZEN = "frozen"


PRESSURE_REASONS = frozenset(
    {BrakeReason.MEMORY_HARD, BrakeReason.MEMORY_SOFT, BrakeReason.CPU, BrakeReason.DOWNSTREAM}
)

# The governor's own rule names (``LimitRule``) mapped to the reason an operator reads.
_RULE_REASON = {
    "mem_hard": BrakeReason.MEMORY_HARD,
    "mem_incident": BrakeReason.MEMORY_HARD,
    "mem_soft": BrakeReason.MEMORY_SOFT,
    "cpu": BrakeReason.CPU,
    "downstream": BrakeReason.DOWNSTREAM,
    "downstream_holdoff": BrakeReason.DOWNSTREAM,
    "latency_hold": BrakeReason.DOWNSTREAM,
    "mem_target": BrakeReason.MEMORY_TARGET,
    "pressure_unknown": BrakeReason.FROZEN,
    "at_ceiling": BrakeReason.NONE,
}
_CAUSE_REASON = {"mem_hard": BrakeReason.MEMORY_HARD, "mem_soft": BrakeReason.MEMORY_SOFT, "cpu": BrakeReason.CPU,
                 "downstream": BrakeReason.DOWNSTREAM, "mem_incident": BrakeReason.MEMORY_HARD}


_BRAKE_REASON_VALUES = frozenset(r.value for r in BrakeReason)


def brake_reason(rule: object, cooldown_cause: object = None) -> BrakeReason:
    """The reason for a governor rule. A cooldown keeps the pressure that started it; an unknown rule
    name is "ramping" (the pool is below its ceiling for no pressure reason)."""
    name = str(rule)
    if name == "cooldown":
        return _CAUSE_REASON.get(str(cooldown_cause), BrakeReason.RAMPING)
    return _RULE_REASON.get(name, BrakeReason.RAMPING)


def pool_state(reason: BrakeReason, in_use: int, limit: int) -> str:
    """``braked`` only for a pressure reason; else ``full`` when every permit is out; else ``open``."""
    if reason in PRESSURE_REASONS:
        return "braked"
    if limit > 0 and in_use >= limit:
        return "full"
    return "open"


# Keys a caller may add to a projected signal: who is publishing and how it is doing, never what it is
# working on. Anything else is dropped.
IDENTITY_KEYS = frozenset(
    {
        "memberId", "service", "pid", "pod", "roles", "seq", "containerStartMs", "draining",
        "restartsRecent", "restartsByRole", "unexpectedRestartAtMs", "loopLagMs",
    }
)
# The scalar identity keys by the type `load_signal_v1.schema.json` gives them; the rest are numbers.
_IDENTITY_STRINGS = frozenset({"memberId", "pod"})
_IDENTITY_INTEGERS = frozenset({"pid", "seq"})
SIGNAL_KEYS = frozenset({"v", "workerCount", "cpu", "mem", "pools", "waiting", "downstream"}) | IDENTITY_KEYS
CPU_KEYS = frozenset({"quota", "util", "psiSomeAvg10", "psiSomeAvg60", "throttledRatio", "saturated"})
MEM_KEYS = frozenset(
    {"pressureRaw", "workingSetRatio", "limitBytes", "psiSomeAvg60", "psiFullAvg60", "oomKillsTotal", "saturated"}
)
POOL_KEYS = frozenset({"ceiling", "limit", "inUse", "waiters", "state", "reason", "reasonForS"})
WAITING_KEYS = frozenset({"oldestNotStartedS", "oldestNotStartedPublishMs"})
PUBLISH_MS_KEYS = ("light", "heavy")
DOWNSTREAM_KEYS = frozenset({"throttles", "timeouts", "exhaustions", "unavailable"})
POOL_STATES = ("braked", "full", "open")

# The services the governor hears about. A name outside the set is counted under ``other``: a
# service name is chosen by the code that reports it, and must never become a label by itself.
DOWNSTREAM_NAMES = frozenset(
    {"neo4j", "arango", "postgres", "redis", "qdrant", "opensearch", "kafka", "llm_gateway", "embedding",
     "docling", "parsing", "extraction", "mongo"}
)

# What a client reports as its name is the client's own spelling. These map the spellings the code
# base uses to the closed names above: the service clients (``BaseServiceClient.service_name``), the
# embedding server client, and every embedding provider the vector store reports (``EmbeddingProvider``).
_DEPENDENCY_ALIASES = {
    "doclingservice": "docling",
    "parsingservice": "parsing",
    "extractionservice": "extraction",
    "embeddingserver": "embedding",
}
EMBEDDING_PROVIDER_NAMES = frozenset(
    {"anthropic", "bedrock", "azureai", "azureopenai", "cohere", "default", "fireworks", "gemini", "huggingface",
     "jinaai", "litellmproxy", "lmstudio", "mistral", "ollama", "openai", "openaicompatible", "openrouter",
     "sentencetransformers", "together", "vertexai", "voyage"}
)  # lower-cased ``EmbeddingProvider`` values (a test pins the list to the enum)


def normalise_dependency(name: str) -> str:
    """The closed dependency name for what a client reported, or ``other``."""
    if name in DOWNSTREAM_NAMES:
        return name
    lowered = name.strip().lower()
    if lowered in DOWNSTREAM_NAMES:
        return lowered
    if lowered in _DEPENDENCY_ALIASES:
        return _DEPENDENCY_ALIASES[lowered]
    if lowered in EMBEDDING_PROVIDER_NAMES:
        return "embedding"
    return "other"


ROLE_NAMES = frozenset(
    {"api", "slackbot", "connectors", "query", "indexing", "parse-small", "parse-large", "docling", "embedding",
     "parsing", "extraction"}
)
_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_Number = (int, float)


def _num(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, _Number):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _dict(value: object) -> dict[Any, Any]:
    """*value* when it is a dict, else an empty one: a wrong shape loses its own field only."""
    return value if isinstance(value, dict) else {}


def _clean_identity(identity: Mapping[str, Any] | None) -> dict[str, Any]:
    """The identity keys that carry closed values: roles and the restart roles come from the role set,
    the service from the service-name shape; the rest are the process's own scalars."""
    out: dict[str, Any] = {}
    for key, value in _dict(identity).items():
        if key not in IDENTITY_KEYS:
            continue
        if key == "roles":
            if isinstance(value, (list, tuple)) and all(isinstance(v, str) and v in ROLE_NAMES for v in value):
                out[key] = list(value)
        elif key == "restartsByRole":
            if isinstance(value, dict) and all(k in ROLE_NAMES and _num(v) is not None for k, v in value.items()):
                out[key] = dict(value)
        elif key == "service":
            if isinstance(value, str) and _SERVICE_NAME.match(value):
                out[key] = value
        elif key in _IDENTITY_STRINGS:
            if isinstance(value, str):
                out[key] = value
        elif key == "draining":
            if isinstance(value, bool):
                out[key] = value
        elif key in _IDENTITY_INTEGERS:
            if isinstance(value, int) and not isinstance(value, bool):
                out[key] = value
        elif _num(value) is not None:  # the nullable numbers (a null is simply absent)
            out[key] = value
    return out


def _pool_rows(admission: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pool in POOLS:
        row = _dict(admission.get("pools")).get(pool)
        if not isinstance(row, dict):
            continue
        held = row.get("brakeReason")  # what the governor tracked across the cooldown, when it says
        reason = BrakeReason(held) if isinstance(held, str) and held in _BRAKE_REASON_VALUES else brake_reason(row.get("rule"), row.get("cooldownCause"))
        limit, in_use = _num(row.get("limit")), _num(row.get("inUse"))
        out[pool] = {
            "ceiling": _num(row.get("ceiling")),
            "limit": limit,
            "inUse": in_use,
            "waiters": _num(row.get("waiting")),
            "state": pool_state(reason, int(in_use or 0), int(limit or 0)) if limit is not None else None,
            "reason": reason.value,
            "reasonForS": _num(row.get("ruleAgeSec")),
        }
    return out


def project(
    stats: Mapping[str, Any],
    identity: Mapping[str, Any] | None = None,
    pressure: PressureSample | None = None,
) -> dict[str, Any]:
    """The load signal body (v1) for one process: ``stats()`` and a pressure sample, reduced to the
    allow-listed keys.

    A value of the wrong type becomes ``None`` (or is left out) instead of passing through, and only
    that field: a wrong shape in one part never loses the rest. A downstream name is mapped to
    ``DOWNSTREAM_NAMES`` by ``normalise_dependency`` and counted as ``other`` when it is not one. Pools come from ``stats()["admission"]``
    when the governor has it; otherwise ``pools`` is empty and the waiting ages are ``None``.
    """
    admission = stats.get("admission") if isinstance(stats.get("admission"), dict) else {}
    waiting = stats.get("waiting") if isinstance(stats.get("waiting"), dict) else {}
    publish = _dict(waiting.get("oldest_not_started_publish_ms"))
    p = pressure
    out: dict[str, Any] = {
        "v": LOAD_SIGNAL_VERSION,
        "workerCount": _num(stats.get("worker_count")),
        "cpu": {
            "quota": _num(stats.get("cpu_quota")),
            "util": _num(stats.get("cpu_utilisation")),
            "psiSomeAvg10": _num(getattr(p, "cpu_psi_some_avg10", None)),
            "psiSomeAvg60": _num(getattr(p, "cpu_psi_some_avg60", None)),
            "throttledRatio": _num(stats.get("cpu_throttled_ratio")),
            "saturated": _bool(getattr(p, "cpu_saturated", None)),
        },
        "mem": {
            "pressureRaw": _num(stats.get("mem_pressure_raw")),
            # Until the governor exposes its reserved bytes, pressureRaw (the admission view) is the same
            # figure as the observed working set; the series help says so.
            "workingSetRatio": _num(stats.get("mem_pressure_raw")),
            "limitBytes": _num(stats.get("mem_limit_bytes")),
            "psiSomeAvg60": _num(getattr(p, "mem_psi_some_avg60", None)),
            "psiFullAvg60": _num(getattr(p, "mem_psi_full_avg60", None)),
            "oomKillsTotal": _num(getattr(p, "oom_kills_total", None)),
            "saturated": _bool(getattr(p, "mem_saturated", None)),
        },
        "pools": _pool_rows(admission),
        "waiting": {
            "oldestNotStartedS": _num(waiting.get("oldest_not_started_s")),
            "oldestNotStartedPublishMs": {
                "light": _num(publish.get("light")),
                "heavy": _num(publish.get("heavy")),
            },
        },
        "downstream": _downstream(_downstream_window(stats, admission)),
    }
    out.update(_clean_identity(identity))
    return out


def _downstream_window(stats: Mapping[str, Any], admission: Mapping[str, Any]) -> Mapping[str, Any]:
    """The downstream window of the sample the pools came from.

    ``stats()`` copies ``downstream_feedback`` and builds the admission block under separate lock holds, so a
    sample can land between them; the admission block's own window is from the same hold as its pools.
    """
    window = _dict(_dict(admission.get("feedback")).get("window"))
    return window if window else _dict(stats.get("downstream_feedback"))


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _downstream(feedback: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    columns = {
        "throttles": "throttles",
        "timeouts": "timeouts",
        "pool_exhaustions": "exhaustions",
        "unavailable": "unavailable",
    }
    out: dict[str, dict[str, int]] = {}
    for source, target in columns.items():
        for name, count in _dict(feedback.get(source)).items():
            value = _num(count)
            if value is None or not isinstance(name, str):
                continue
            key = normalise_dependency(name)
            row = out.setdefault(key, dict.fromkeys(columns.values(), 0))
            row[target] += int(value)
    return dict(sorted(out.items()))
