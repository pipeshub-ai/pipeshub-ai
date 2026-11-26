"""EG-D1..EG-D5 of the durable E2E lane (E2E2): nothing left a sandbox except through the gateways (C-13's
assertions, read from the stack's own logs after the specs ran).

    python3 check_egress.py --durable <logs dir> <proxy address on the sandbox plane> <app address>

Prints one line per check; exits 1 if any fails. Standard library only. Stage 1's checks (EG-1..EG-4, the query
service's tool gateway) went with stage 1 (S1R).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path



def durable(logs: Path, proxy_ip: str, app_ip: str) -> int:
    """EG-D1..EG-D4 of the durable lane (E2E2): engine runs on the worker, their tools through its gateway.

    Reads ``egress-proxy.log``, ``worker.log``, ``model-gateway.log``, the scripted model's request log after the
    durable specs (``fake-llm-durable.json``) and the scripted SaaS's effects (``fake-saas.json``).
    """
    failures: list[str] = []

    def check(case: str, ok: bool, detail: str) -> None:  # noqa: FBT001
        print(f"  {'ok  ' if ok else 'FAIL'} {case} {detail}")
        if not ok:
            failures.append(case)

    proxy = (logs / "egress-proxy.log").read_text(errors="replace")
    worker = (logs / "worker.log").read_text(errors="replace")
    gateway = (logs / "model-gateway.log").read_text(errors="replace")
    model = json.loads((logs / "fake-llm-durable.json").read_text())["requests"]
    # Every effect since the SaaS started (the specs reset its control list between them).
    effects = [
        json.loads(line[line.index("{"):])["fake_saas"]
        for line in (logs / "fake-saas.log").read_text(errors="replace").splitlines()
        if '{"fake_saas"' in line
    ]

    refused = [line for line in proxy.splitlines() if "egress.refused" in line]
    check("EG-D1", not refused, f"the proxy refused nothing ({len(refused)} egress.refused lines)")
    bound = [line for line in proxy.splitlines() if "egress.bound" in line]
    sandboxes = {m.group(1) for line in bound for m in [re.search(r"sandboxId=(\S+)", line)] if m}
    check("EG-D1", bool(sandboxes) and all("destinations=2" in line for line in bound),
          f"every sandbox was bound to exactly the two gateways ({len(sandboxes)} sandboxes)")

    gateway_refusals = [line for line in worker.splitlines() if "gateway.refused" in line or "gateway_token.unavailable" in line]
    check("EG-D2", not gateway_refusals, f"the worker's tool gateway refused nothing ({len(gateway_refusals)} lines)")
    check("EG-D2", bool(effects), f"the engine's tool calls reached the worker's gateway through the proxy ({len(effects)} effects)")

    calls = [line for line in gateway.splitlines() if "model_gateway.call " in line]
    gateway_refused = [line for line in gateway.splitlines() if "model_gateway.refused" in line]
    check("EG-D3", bool(calls) and not gateway_refused,
          f"the model gateway served {len(calls)} calls on request tokens and refused none")
    runs = {m.group(1) for line in calls for m in [re.search(r"runId=(\S+)", line)] if m}
    check("EG-D3", bool(runs), f"every model call named its run ({len(runs)} runs)")

    engine = [r for r in model if r.get("kind") == "engine"]
    # Every call the gateway sent on is one ``model_gateway.call`` line however it ended: a caller gone before the
    # first event or mid-answer (the crash case's held call, a reaped sandbox) is a cut call, status=interrupted
    # (FIX-EGD4). ``client_gone`` is logged beside that line, never instead of it, so it is not added again.
    cut = [line for line in calls if "status=interrupted" in line]
    check("EG-D4", all(r.get("status") == 200 for r in model),
          "the model got only calls it accepts (the org key, a configured model, well-formed messages)")
    check("EG-D4", len(engine) == len(calls),
          f"every engine call reached the model through the model gateway and was recorded there ({len(engine)} "
          f"engine calls, {len(calls)} gateway calls, {len(cut)} of them cut when their caller went away)")
    check("EG-D4", {r.get("peer") for r in engine} <= {app_ip},
          f"engine calls came only from the model gateway's container ({sorted({str(r.get('peer')) for r in engine})})")
    peers = {e.get("peer") for e in effects}
    check("EG-D4", peers <= {app_ip}, f"the SaaS saw only the worker ({sorted(map(str, peers))}, app {app_ip}, proxy {proxy_ip})")

    # DE-5 from the worker's side: the crashed run's second attempt, on a fresh process, answered the re-call
    # from the journal instead of running the tool again.
    crashed_file = logs.parent / "control" / "crashed-run"
    if crashed_file.exists():
        run_id = crashed_file.read_text().strip()
        supervisor = (logs / "worker-supervisor.log").read_text(errors="replace")
        check("DE-5", "killed on request" in supervisor and "worker process 2 started" in supervisor,
              "the worker was SIGKILLed and a fresh process started")
        claims = [line for line in worker.splitlines() if "run.claimed" in line and f"runId={run_id}" in line]
        fences = sorted({int(m.group(1)) for line in claims for m in [re.search(r"fence=(\d+)", line)] if m})
        # An attempt between the crash and the one that finishes may release the run (a broker call that timed out
        # on a loaded Docker daemon: `journal.release reason=sandbox_broker_unavailable`); that is recovery too.
        # What must hold: a later attempt finished the run and answered the re-call from the journal.
        check("DE-5", fences[:1] == [1] and len(fences) >= 2, f"the run was claimed again after the crash (fences {fences})")
        last = next((m.group(1) for line in claims if f"fence={fences[-1]}" in line for m in [re.search(r"attemptId=(\S+)", line)] if m), None)
        recorded = [line for line in worker.splitlines()
                    if "tool.dedupe" in line and "choice=recorded" in line and last is not None and f"inv_{last}_" in line]
        check("DE-5", len(recorded) == 1, f"the finishing attempt's re-call was answered from the journal ({len(recorded)} recorded)")
    else:
        check("DE-5", False, "the crash spec recorded no run (control/crashed-run missing)")

    if failures:
        print(f"egress checks failed: {sorted(set(failures))}")
        return 1
    print("egress checks passed")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] != ["--durable"] or len(sys.argv) != 5:
        sys.exit("usage: check_egress.py --durable <logs dir> <proxy address> <app address>")
    sys.exit(durable(Path(sys.argv[2]), sys.argv[3], sys.argv[4]))
