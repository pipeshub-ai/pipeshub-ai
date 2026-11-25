"""TEST-ONLY: the checks airgap_install_smoke.sh runs on an install made by install.sh (C15.04, C20.48).

    python3 install_checks.py <run dir> turn            a scripted chat turn ends with its answer
    python3 install_checks.py <run dir> job             an engine agent's turn hands a job to the coding engine,
                                                         which runs a shell command and finishes
    python3 install_checks.py <run dir> docling         the Docling service parses a PDF with its own models
    python3 install_checks.py <run dir> record FILE     DR drill: a conversation and a job, written to FILE
    python3 install_checks.py <run dir> after-backup FILE  DR drill: one more conversation, which a restore loses
    python3 install_checks.py <run dir> verify FILE     DR drill: after the restore, what FILE names is back (the
                                                         conversation's answer, the job finished), the conversation
                                                         made after the backup is gone, and a new turn works

The install is attached as a stack of deployment/e2e (lane.Stack): the run dir holds stack.env and seed.json
(seed.py's state). No live model: every turn runs on the scripted model inside the stack. One line per check;
exit 1 when any failed.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path[:0] = [str(REPO / "deployment/e2e"), str(REPO / "integration-tests/harness/e2e")]

Json = dict[str, Any]
FAILED: list[str] = []

# One page, one line of text: the smallest PDF the layout model still has to look at. Offsets are computed.
_PDF_OBJECTS = (
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
    b" /Resources << /Font << /F1 5 0 R >> >> >>",
    None,
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
)
_PDF_TEXT = b"BT /F1 18 Tf 72 700 Td (Air-gapped Docling check) Tj ET"


def tiny_pdf() -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    stream = b"<< /Length %d >>\nstream\n" % len(_PDF_TEXT) + _PDF_TEXT + b"\nendstream"
    for number, body in enumerate(_PDF_OBJECTS, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + (stream if body is None else body) + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets) + 1, xref)
    return bytes(out)


def check(name: str, ok: bool, detail: str = "") -> bool:  # noqa: FBT001 - smoke_human.check's shape
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{f' ({detail})' if detail else ''}", flush=True)  # noqa: T201 - the check's report
    if not ok:
        FAILED.append(name)
    return ok


def attach(run_dir: Path):  # noqa: ANN201 - lane.Stack, imported late (it reads E2E_STATE_DIR at import)
    os.environ["E2E_STATE_DIR"] = str(run_dir.parent)
    from lane import Stack

    return Stack.attach(run_dir.name, "install")


def chat_turn(stack, marker: str) -> Json:  # noqa: ANN001
    from smoke_human import TERMINAL, answer, turn

    frames, session, run = turn(stack, stack.seed["agentKey"], f"say {marker}")
    last = frames[-1].type if frames else "none"
    ok = bool(run) and last in TERMINAL and marker in answer(frames)
    check(f"scripted chat turn answers {marker}", ok, f"conversation {session}, run {run}, ended {last}")
    return {"agentKey": stack.seed["agentKey"], "session": session, "run": run, "marker": marker}


def jobs_of(stack, agent_key: str, session: str, run: str) -> Json:  # noqa: ANN001
    got = stack.api.request("GET", f"/api/v1/agents/{agent_key}/conversations/{session}/runs/{run}/jobs")
    return got.body if got.status == 200 and isinstance(got.body, dict) else {"status": got.status, "jobs": []}


def engine_job(stack, marker: str) -> Json:  # noqa: ANN001
    from smoke_human import answer, handed_off, turn

    key = stack.seed["engineAgentKey"]
    frames, session, run = turn(stack, key, f"shell echo {marker}\nsay {marker}")
    last = frames[-1].type if frames else "none"
    child = handed_off(frames)
    check("an engine agent's turn hands its work to a job and finishes", bool(child) and last == "RUN_FINISHED",
          f"conversation {session}, run {run}, job {child or 'none'}, ended {last}")
    check(f"the turn's answer carries {marker}", marker in answer(frames))
    jobs = jobs_of(stack, key, session, run)
    mine = [j for j in jobs.get("jobs") or [] if j.get("runId") == child]
    check("the request lists its job as finished", bool(mine) and mine[0].get("state") == "finished",
          json.dumps(mine or jobs)[:300])
    return {"agentKey": key, "session": session, "run": run, "job": child, "marker": marker,
            "engine": (mine[0].get("engine") if mine else None)}


def docling(stack) -> None:  # noqa: ANN001
    probe = (
        "import json, sys, urllib.request, uuid\n"
        "pdf = sys.stdin.buffer.read(); b = uuid.uuid4().hex\n"
        "parts = [f'--{b}\\r\\nContent-Disposition: form-data; name=\"record_name\"\\r\\n\\r\\nairgap.pdf\\r\\n'.encode(),\n"
        "  f'--{b}\\r\\nContent-Disposition: form-data; name=\"file\"; filename=\"airgap.pdf\"\\r\\n'\n"
        "  'Content-Type: application/pdf\\r\\n\\r\\n'.encode() + pdf + b'\\r\\n', f'--{b}--\\r\\n'.encode()]\n"
        "req = urllib.request.Request('http://localhost:8081/parse-pdf', data=b''.join(parts), method='POST',\n"
        "  headers={'Content-Type': f'multipart/form-data; boundary={b}'})\n"
        "try:\n"
        "    body = json.load(urllib.request.urlopen(req, timeout=600))\n"
        "except urllib.error.HTTPError as e:\n"
        "    body = {'success': False, 'error': f'{e.code} {e.read()[:300]!r}'}\n"
        "print(json.dumps({'success': body.get('success'), 'error': body.get('error'),\n"
        "  'text': 'Air-gapped Docling check' in (body.get('parse_result') or '')}))\n"
    )
    app = stack.runtime.container("pipeshub-ai")
    done = subprocess.run(["docker", "exec", "-i", app, "python", "-c", probe], input=tiny_pdf(),
                          capture_output=True, timeout=900, check=False)
    try:
        result = json.loads(done.stdout.decode().strip().splitlines()[-1])
    except (IndexError, ValueError):
        result = {"success": False, "error": done.stderr.decode()[-300:]}
    check("Docling parses a PDF with no route out (HF_HUB_OFFLINE=1)", result.get("success") is True and result["text"],
          str(result.get("error") or "")[:300])


def conversation_body(stack, agent_key: str, session: str) -> tuple[int, str]:  # noqa: ANN001
    got = stack.api.request("GET", f"/api/v1/agents/{agent_key}/conversations/{session}")
    return got.status, json.dumps(got.body)


def verify(stack, record: Json) -> None:  # noqa: ANN001
    conv = record["conversation"]
    status, body = conversation_body(stack, conv["agentKey"], conv["session"])
    check("the conversation from before the backup opens with its answer", status == 200 and conv["marker"] in body,
          f"HTTP {status}")
    job = record["job"]
    jobs = jobs_of(stack, job["agentKey"], job["session"], job["run"])
    mine = [j for j in jobs.get("jobs") or [] if j.get("runId") == job["job"]]
    check("the job from before the backup is listed, finished", bool(mine) and mine[0].get("state") == "finished",
          json.dumps(mine or jobs)[:300])
    status, body = conversation_body(stack, job["agentKey"], job["session"])
    check("the job's conversation opens with its answer", status == 200 and job["marker"] in body, f"HTTP {status}")
    lost = record.get("lost")
    if lost:
        status, body = conversation_body(stack, lost["agentKey"], lost["session"])
        check("the conversation made after the backup is gone (the measured RPO)",
              status == 404 or lost["marker"] not in body, f"HTTP {status}")
    deps = stack.api.request("GET", "/api/v1/agent-harness/admin/dependencies")
    check("the admin dependencies read ok", deps.status == 200 and "degraded" not in json.dumps(deps.body),
          json.dumps(deps.body)[:300])
    chat_turn(stack, f"after-{secrets.token_hex(3)}")
    engine_job(stack, f"after-job-{secrets.token_hex(3)}")


def main() -> int:
    run_dir, command, *rest = Path(sys.argv[1]).resolve(), *sys.argv[2:]
    stack = attach(run_dir)
    if command == "turn":
        chat_turn(stack, f"airgap-{secrets.token_hex(3)}")
    elif command == "job":
        engine_job(stack, f"airgap-job-{secrets.token_hex(3)}")
    elif command == "docling":
        docling(stack)
    elif command == "record":
        record = {"conversation": chat_turn(stack, f"dr-{secrets.token_hex(3)}"),
                  "job": engine_job(stack, f"dr-job-{secrets.token_hex(3)}")}
        Path(rest[0]).write_text(json.dumps(record, indent=2))
    elif command == "after-backup":
        record = json.loads(Path(rest[0]).read_text())
        record["lost"] = chat_turn(stack, f"lost-{secrets.token_hex(3)}")
        Path(rest[0]).write_text(json.dumps(record, indent=2))
    elif command == "verify":
        verify(stack, json.loads(Path(rest[0]).read_text()))
    else:
        sys.exit(__doc__)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
