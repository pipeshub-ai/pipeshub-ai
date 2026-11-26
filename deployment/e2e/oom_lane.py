"""R31GVISO-FU-3 (R32-SECCOMP): an engine job whose work meets its sandbox's memory limit, on a human-style stack.

    python3 oom_lane.py <run dir>

The seeded opencode coder agent's engine child runs a command that allocates without bound (scripted model). What
each runtime does with it is the property checked:

* gVisor: the host's OOM killer sees one process, the Sentry, so the whole sandbox ends. The child run fails once as
  ``out_of_memory`` with its sandbox's limit (``run.failed.memoryLimitMb``), never released and driven again; the
  person's stream ends the job with that code, and ``child.completed`` and the job's part carry the limit, so the job
  card reads "ran out of memory (limit N)" (R32SECCOMP-FU-4).
* runc: the kernel kills the largest process in the sandbox's cgroup. When that is the engine itself (one of two runs
  in testing), the run ends as on gVisor: ``out_of_memory`` once, with the limit. When it is the command, the
  engine reads its exit (137) and goes on, and nothing reads as an out-of-memory end.
* A loop run's own ``run_code`` (the seeded loop agent in deep mode, R32SECCOMP-FU-1): the same allocation is a failed
  run whose result names the limit ("The code ran out of memory (limit N)"), on either runtime; the run goes on and
  answers, never a tool failure or a run end.

Prints one line per check and exits non-zero when one fails.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RUN_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path()
os.environ.setdefault("E2E_STATE_DIR", str(RUN_DIR.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integration-tests" / "harness" / "e2e"))

import smoke_human as sh  # noqa: E402

# smoke_human reads the run's logs from its own RUN_DIR, set only when it runs as a script.
sh.RUN_DIR = RUN_DIR

# Grows by 64 MiB of written pages until something stops it; one shell line for the scripted model.
BALLOON = "python3 -c \"b=[]; [b.append(b'x'*(64<<20)) for _ in iter(int, 1)]\""
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}{f' ({detail})' if detail else ''}", flush=True)
    if not ok:
        FAILED.append(name)


def check_oom_job(stack: sh.Stack) -> None:
    runtime = stack.env.get("E2E_SANDBOX_RUNTIME") or "auto"
    frames, session, run = sh.turn(stack, stack.seed["agentOpencode"], f"shell {BALLOON}\nsay survived")
    child = sh.handed_off(frames)
    lines = sh.worker_lines(child) if child else []
    oom = [line for line in lines if "engine.sandbox.out_of_memory" in line]
    failed = [line for line in lines if "engine.kernel.failed" in line and "reasonCode=out_of_memory" in line]
    released = [line for line in lines if "engine.kernel.release" in line]
    child_errors = [f.data for f in frames if f.type == "RUN_ERROR" and f.data.get("parentRunId")]
    print(f"runtime {runtime}: run {run} child {child or 'none'}; last frame {frames[-1].type if frames else 'none'}")
    check("the coder agent handed the command to its opencode engine child", bool(child))
    gvisor = runtime == "runsc" or any("mode=gvisor" in line for line in lines)
    limit = oom[0].split("memoryMb=", 1)[1].split()[0] if oom else ""
    if gvisor:
        check("gVisor: the memory limit ended the whole sandbox, and the controller found it", bool(oom), f"{len(oom)} lines")
    if oom:
        check("an OOM end fails the child once as out_of_memory, never released and driven again",
              len(failed) == 1 and not released, f"failed {len(failed)}, released {len(released)}, limit {limit} MB")
        check("the person's stream ends the job with out_of_memory, which the job card words",
              any(e.get("errorType") == "out_of_memory" and e.get("runId") == child for e in child_errors),
              json.dumps(child_errors)[:300])
        ended = [e.get("data") or {} for e in stack.journal(session)["events"]
                 if e.get("type") == "child.completed" and (e.get("data") or {}).get("childRunId") == child]
        named = [d.get("memoryLimitMb") for d in ended]
        check("child.completed names the limit, so the job card reads it (R32SECCOMP-FU-4)",
              bool(limit) and named == [int(limit)], f"child.completed memoryLimitMb {named}, controller {limit} MB")
    else:
        # runc: the kernel killed the command, not the engine; the engine read its exit and went on.
        check("runc: the command was killed and the engine went on, nothing read as an OOM end",
              not failed and bool(frames) and frames[-1].type in sh.TERMINAL, frames[-1].type if frames else "none")

def check_oom_run_code(stack: sh.Stack) -> None:
    code = {"language": "python", "code": "b = []\nwhile True:\n    b.append(b'x' * (64 << 20))", "timeout": 120}
    text = f"Please do the steps below.\ntool run_code {json.dumps(code)}\nsay ran-on"
    frames, _session, run = sh.turn(stack, stack.seed["agentLoop"], text, mode="deep")
    results = json.dumps([f.data for f in frames if f.type == "TOOL_CALL_RESULT"])
    named = "ran out of memory (limit" in results
    last = frames[-1].type if frames else "none"
    print(f"run_code: run {run}; last frame {last}")
    check("run_code: the memory limit reads as a failed run naming its limit (R32SECCOMP-FU-1)", named, results[:300])
    check("run_code: the run goes on and answers; an OOM is no run end", last == "RUN_FINISHED" and "ran-on" in sh.answer(frames),
          last)


def main() -> int:
    stack = sh.Stack.attach(RUN_DIR.name, "ST-1")
    check_oom_job(stack)
    check_oom_run_code(stack)
    print(f"oom lane: {'FAIL ' + ', '.join(FAILED) if FAILED else 'all passed'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
