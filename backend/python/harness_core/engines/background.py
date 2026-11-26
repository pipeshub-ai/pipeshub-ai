"""Commands an engine's shell tool left running in its sandbox when its turn ended (LCFIX).

An engine's shell tool may return while what it started still runs (Codex's ``exec_command`` yields after about
10 s while ``sleep 40`` runs). The sandbox is released at turn end, so such a command is cut off silently. The
controller lists the sandbox's processes before the turn and after it; of the ones the turn started that still run,
it stops and reports only those it can attribute to the engine's shell tool: a shell the turn started and what it
runs, and the children the turn started of the processes the adapter names as running each command
(``EngineCapabilities.command_parents``, Codex's app-server). Anything else (the engine's own workers, a language
server, the sandbox's relay) is left alone; a command it cannot attribute is not reported either.

The listing is ``/proc`` read by ``sh`` (every engine image has both): no process tool is assumed. Anything it
cannot read is reported as unknown, never as a failure of the turn.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from harness_core.sandbox.streams import collect

if TYPE_CHECKING:
    from harness_core.sandbox.ports import SandboxHandle

# The probe's own pid first, then every process's stat line (the probe and its ``cat`` are told apart by it).
LIST_SCRIPT = 'echo "$$"; cat /proc/[0-9]*/stat 2>/dev/null; exit 0'
STOP_SCRIPT = 'kill -9 "$@" 2>/dev/null; exit 0'
PROBE_TIMEOUT_S = 10.0
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ash", "fish"})
_MAX_BYTES = 4 * 1024 * 1024

# (pid, start time in clock ticks): a pid reused by a later process is another process.
type ProcessKey = tuple[int, int]
# What is known of each process: its parent's pid and its executable name.
type ProcessTable = dict[ProcessKey, tuple[int, str]]


def parse_table(output: str) -> ProcessTable | None:
    """``LIST_SCRIPT``'s output as a table, without the probe and its children and without zombies (already dead)."""
    lines = output.splitlines()
    if not lines or not lines[0].strip().isdigit():
        return None
    probe = int(lines[0])
    table: ProcessTable = {}
    for line in lines[1:]:
        pid_text, _, rest = line.partition(" (")
        comm, _, tail = rest.rpartition(") ")
        fields = tail.split()
        if not pid_text.isdigit() or len(fields) < 20 or not fields[1].lstrip("-").isdigit() or not fields[19].isdigit():
            continue
        pid, state, ppid = int(pid_text), fields[0], int(fields[1])
        if probe in (pid, ppid) or state == "Z":
            continue
        table[(pid, int(fields[19]))] = (ppid, comm)
    return table


def shell_commands(
    before: ProcessTable, after: ProcessTable, command_parents: frozenset[str] = frozenset()
) -> tuple[set[int], int]:
    """Of the processes started since ``before`` that still run, those the shell tool ran (their pids), and how many
    commands they are (a command and what it started count once)."""
    started = {pid: value for (pid, start), value in after.items() if (pid, start) not in before}
    runners = {pid for (pid, _), (_, comm) in before.items() if comm in command_parents}

    def under_shell(pid: int) -> bool:
        seen: set[int] = set()
        while pid in started and pid not in seen:
            seen.add(pid)
            ppid, comm = started[pid]
            if comm in SHELLS or ppid in runners:
                return True
            pid = ppid
        return False

    attributed = {pid for pid in started if under_shell(pid)}
    roots = sum(1 for pid in attributed if started[pid][0] not in attributed)
    return attributed, roots


async def _run(sandbox: SandboxHandle, argv: list[str]) -> str | None:
    process = await sandbox.exec(argv, env={})
    try:
        out, _, code = await asyncio.wait_for(
            asyncio.gather(collect(process.stdout, limit=_MAX_BYTES), collect(process.stderr), process.wait()),
            PROBE_TIMEOUT_S,
        )
    except (TimeoutError, ValueError):
        with contextlib.suppress(Exception):
            await process.signal(9)
        return None
    return out.decode("utf-8", "replace") if code == 0 else None


async def process_table(sandbox: SandboxHandle) -> ProcessTable | None:
    """The sandbox's processes now; ``None`` when they cannot be listed."""
    try:
        output = await _run(sandbox, ["sh", "-c", LIST_SCRIPT])
    except Exception:
        return None
    return parse_table(output) if output is not None else None


async def stop_started(
    sandbox: SandboxHandle, before: ProcessTable, command_parents: frozenset[str] = frozenset()
) -> int | None:
    """Stops the shell commands started since ``before`` that still run; how many they were, ``None`` when the
    sandbox's processes cannot be listed."""
    after = await process_table(sandbox)
    if after is None:
        return None
    pids, commands = shell_commands(before, after, command_parents)
    if pids:
        with contextlib.suppress(Exception):
            _ = await _run(sandbox, ["sh", "-c", STOP_SCRIPT, "sh", *(str(pid) for pid in sorted(pids))])
    return commands


def stopped_note(count: int) -> str:
    """What the turn's answer says about them."""
    if count == 1:
        return "1 background command was still running when the turn ended and was stopped."
    return f"{count} background commands were still running when the turn ended and were stopped."


__all__ = [
    "LIST_SCRIPT",
    "SHELLS",
    "STOP_SCRIPT",
    "ProcessTable",
    "parse_table",
    "process_table",
    "shell_commands",
    "stop_started",
    "stopped_note",
]
