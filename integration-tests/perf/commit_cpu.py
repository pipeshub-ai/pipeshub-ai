"""What a harness request costs one Node process (PERF-D; finding PERFC-F1).

Requests that arrive in the same instant share one Node event loop: k of them that each need c ms of it end about
k x c ms after they arrived, whatever the store does. So the number that decides SLO #1 and SLO #4 for sessions in
step is the event-loop CPU of one request, and it is measured as CPU, not as latency:

* **the total** is the Node process's own count of its event-loop thread's CPU over a window of turns driven one
  at a time, less what it burns idle, per turn (``Window`` of a plain run);
* **whose it is** comes from a second run in which Node times every callback and charges it to the request whose
  async context it ran in (``tickUs``). The hooks slow promises down, so only that run's shares are used;
* **the store commands** each request sent are counted in the second run too. They do not depend on the host.

A loaded host stretches CPU time as well as wall time, so a result also carries the CPU of a fixed calibration
workload on the same thread in the same window, and costs are stated in that unit as well as in ms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Sessions in step in the gated profile (``commits-n16-instep``).
IN_STEP = 16
# The requests SLO #1 and SLO #4 time, as the fault stack's ledger names them.
ADMISSION = "admission"
STEP_COMMIT = "commit:step"
ENDING_COMMIT = "commit:completed"
GATED_KINDS = (ADMISSION, STEP_COMMIT, ENDING_COMMIT)
# Charged per turn, not per request: the stream an admission opened, and what no request asked for.
STREAM = "admission:stream"
BACKGROUND = "background"


@dataclass(frozen=True)
class Window:
    """One measured window of ``turns`` turns and the idle window before it, as the ledger reports them."""

    turns: int
    loop_us: float
    wall_ms: float
    idle_loop_us: float
    idle_wall_ms: float
    calibration_us: float
    requests: dict[str, int]
    tick_us: dict[str, float]
    idle_tick_us: dict[str, float]
    commands: dict[str, int]

    @classmethod
    def of(cls, turns: int, idle: dict[str, Any], run: dict[str, Any], calibration_us: float) -> Window:
        return cls(turns=turns, loop_us=float(run["loopUs"]), wall_ms=float(run["wallMs"]),
                   idle_loop_us=float(idle["loopUs"]), idle_wall_ms=float(idle["wallMs"]),
                   calibration_us=calibration_us, requests=dict(run.get("requests") or {}),
                   tick_us=dict(run.get("tickUs") or {}), idle_tick_us=dict(idle.get("tickUs") or {}),
                   commands=dict(run.get("commands") or {}))

    def turn_cpu_ms(self) -> float:
        """Event-loop CPU of one turn, less what the process burns idle over the same wall time."""
        idle_rate = self.idle_loop_us / self.idle_wall_ms if self.idle_wall_ms > 0 else 0.0
        return (self.loop_us - idle_rate * self.wall_ms) / self.turns / 1000

    def shares(self) -> dict[str, float]:
        """Each kind's share of the callbacks' time; the background's idle part is not a turn's."""
        ticks = dict(self.tick_us)
        if self.idle_wall_ms > 0 and BACKGROUND in ticks:
            idle = self.idle_tick_us.get(BACKGROUND, 0.0) / self.idle_wall_ms * self.wall_ms
            ticks[BACKGROUND] = max(0.0, ticks[BACKGROUND] - idle)
        total = sum(ticks.values())
        return {kind: us / total for kind, us in ticks.items()} if total > 0 else {}

    def per_turn(self, kind: str) -> float:
        """Requests of ``kind`` a turn; 1 for what is charged per turn."""
        return self.requests.get(kind, 0) / self.turns if kind in self.requests else 1.0

    def commands_per_request(self) -> dict[str, float]:
        """Store commands by the kind of request that sent them (per turn for the stream and the background)."""
        by_kind: dict[str, int] = {}
        for key, count in self.commands.items():
            kind = key.split(" | ", 1)[0]
            by_kind[kind] = by_kind.get(kind, 0) + count
        return {kind: count / (self.per_turn(kind) * self.turns) for kind, count in by_kind.items()}

    def commands_per_turn(self) -> float:
        return sum(self.commands.values()) / self.turns


def request_cpu_ms(total: Window, attributed: Window) -> dict[str, float]:
    """Event-loop CPU of one request of each kind: its share of the callbacks, applied to the plain run's turn."""
    turn_ms = total.turn_cpu_ms()
    return {kind: share * turn_ms / attributed.per_turn(kind) for kind, share in attributed.shares().items()}


def in_step_ms(cost_ms: float, sessions: int = IN_STEP) -> float:
    """When the last of ``sessions`` requests of the same instant ends, if each needs ``cost_ms`` of the loop."""
    return sessions * cost_ms


def _one(value: float) -> dict[str, Any]:
    rounded = round(value, 3)
    return {"n": 1, "p50": rounded, "p95": rounded, "p99": rounded, "max": rounded}


def metrics(total: Window, attributed: Window) -> dict[str, dict[str, Any]]:
    """The profile's result: per turn and per request, in ms, in calibration units, and in store commands."""
    unit_ms = total.calibration_us / 1000
    out: dict[str, dict[str, Any]] = {
        "turn_cpu_ms": _one(total.turn_cpu_ms()),
        "calibration_ms": _one(unit_ms),
        "commands[turn]": _one(attributed.commands_per_turn()),
    }
    if unit_ms > 0:
        out["turn_cpu_units"] = _one(total.turn_cpu_ms() / unit_ms)
    for kind, cost in sorted(request_cpu_ms(total, attributed).items()):
        out[f"cpu_ms[{kind}]"] = _one(cost)
        if unit_ms > 0:
            out[f"cpu_units[{kind}]"] = _one(cost / unit_ms)
        if kind in GATED_KINDS:
            out[f"in_step_ms[{kind}]"] = _one(in_step_ms(cost))
    for kind, count in sorted(attributed.commands_per_request().items()):
        out[f"commands[{kind}]"] = _one(count)
    return out


def render(result: dict[str, dict[str, Any]]) -> str:
    """The per-request table of a result's metrics, with the arithmetic for ``IN_STEP`` requests of one instant."""
    def value(name: str) -> str:
        found = result.get(name)
        return "—" if found is None else f"{found['max']:g}"

    kinds = sorted(name[len("cpu_ms["):-1] for name in result if name.startswith("cpu_ms["))
    lines = [f"| Request | CPU ms | units | store commands | {IN_STEP} in step end after, ms |", "|---|---|---|---|---|"]
    lines += [f"| {kind} | {value(f'cpu_ms[{kind}]')} | {value(f'cpu_units[{kind}]')} | {value(f'commands[{kind}]')} | "
              f"{value(f'in_step_ms[{kind}]')} |" for kind in kinds]
    lines.append(f"| a turn | {value('turn_cpu_ms')} | {value('turn_cpu_units')} | {value('commands[turn]')} | — |")
    return "\n".join(lines) + "\n"


__all__ = ["ADMISSION", "BACKGROUND", "ENDING_COMMIT", "GATED_KINDS", "IN_STEP", "STEP_COMMIT", "STREAM", "Window",
           "in_step_ms", "metrics", "render", "request_cpu_ms"]
