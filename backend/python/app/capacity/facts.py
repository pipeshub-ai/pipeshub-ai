"""``CapacityFacts``: what this container has. Data only; sources are in :mod:`.sources`."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

FACTS_VERSION = 1
FACTS_ENV = "PIPESHUB_CAPACITY_FACTS"
# An exported payload is a few hundred bytes; anything near this is not ours.
MAX_FACTS_BYTES = 16 * 1024


@dataclass(frozen=True)
class CapacityFacts:
    v: int = FACTS_VERSION
    cpu_quota: float | None = None  # CPUs of the cgroup limit; None is no limit
    cpu_affinity: int = 1
    cpu_host: int = 1
    cpu_request: float | None = None  # k8s requests.cpu, used only without a limit
    mem_limit: int | None = None  # bytes; None is no limit
    mem_host: int = 0
    mem_request: int | None = None  # k8s requests.memory, used only without a limit
    swap_limit: int | None = None  # recorded, never planned into
    cgroup: str = "none"  # "v2" | "v1" | "none"
    numa: tuple[tuple[int, tuple[int, ...]], ...] = ()
    gpus: tuple[tuple[int, int, int], ...] = ()  # (index, mem_total, mem_used)
    member_id: str = ""
    local_cpu_embedding: bool = False
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": self.v,
                "cpu_quota": self.cpu_quota,
                "cpu_affinity": self.cpu_affinity,
                "cpu_host": self.cpu_host,
                "cpu_request": self.cpu_request,
                "mem_limit": self.mem_limit,
                "mem_host": self.mem_host,
                "mem_request": self.mem_request,
                "swap_limit": self.swap_limit,
                "cgroup": self.cgroup,
                "numa": [[n, list(c)] for n, c in self.numa],
                "gpus": [list(g) for g in self.gpus],
                "member_id": self.member_id,
                "local_cpu_embedding": self.local_cpu_embedding,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


def _number(value: object, *, minimum: float = 0.0, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    if not math.isfinite(value) or value < minimum:
        raise ValueError("out of range")
    return float(value)


def facts_from_json(text: str) -> CapacityFacts:
    """Parse an exported payload. Raises ``ValueError`` for anything that is not exactly ours."""
    if not isinstance(text, str) or len(text.encode("utf-8", "replace")) > MAX_FACTS_BYTES:
        raise ValueError("facts payload too large")
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ValueError("facts payload is not JSON") from exc
    if not isinstance(raw, dict) or raw.get("v") != FACTS_VERSION:
        raise ValueError("unknown facts version")
    try:
        cgroup = raw["cgroup"]
        member_id = raw["member_id"]
        if cgroup not in ("v2", "v1", "none") or not isinstance(member_id, str) or len(member_id) > 128:
            raise ValueError("bad enum")
        numa = tuple(
            (int(n), tuple(int(c) for c in cpus)) for n, cpus in _bounded(raw.get("numa", []), 1024)
        )
        gpus = tuple(
            (int(i), int(t), int(u)) for i, t, u in _bounded(raw.get("gpus", []), 64)
        )
        quota = _number(raw.get("cpu_quota"), minimum=0.0, allow_none=True)
        request = _number(raw.get("cpu_request"), minimum=0.0, allow_none=True)
        mem_limit = _number(raw.get("mem_limit"), allow_none=True)
        mem_request = _number(raw.get("mem_request"), allow_none=True)
        swap = _number(raw.get("swap_limit"), allow_none=True)
        affinity = _number(raw["cpu_affinity"], minimum=1.0)
        host = _number(raw["cpu_host"], minimum=1.0)
        mem_host = _number(raw["mem_host"])
        local = raw.get("local_cpu_embedding", False)
        if not isinstance(local, bool):
            raise ValueError("bad flag")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:  # OverflowError: ints too large for a double
        raise ValueError("facts payload does not match the schema") from exc
    return CapacityFacts(
        cpu_quota=quota,
        cpu_affinity=int(affinity or 1),
        cpu_host=int(host or 1),
        cpu_request=request,
        mem_limit=None if mem_limit is None else int(mem_limit),
        mem_host=int(mem_host or 0),
        mem_request=None if mem_request is None else int(mem_request),
        swap_limit=None if swap is None else int(swap),
        cgroup=cgroup,
        numa=numa,
        gpus=gpus,
        member_id=member_id,
        local_cpu_embedding=local,
    )


def _bounded(value: object, limit: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("bad list")
    return value
