"""CI ratchets for org context (direct_org_reads, detached_work).

Counts per file may only go down. To record an intentional change run
``ORG_CONTEXT_UPDATE_BASELINE=1 pytest tests/unit/core/test_org_context_guards.py``
and review the diff of ``org_context_baseline.json``.

detached_work: long-lived work (loops, consumers, lazily created clients) starts with
``spawn_detached``; a bare ``create_task``/``ensure_future`` copies the creating
request's context, so the task would serve every later event as that org.
direct_org_reads: new reads of the org straight off claims or payloads bypass the context.
"""

import ast
import json
import os
from pathlib import Path

APP = Path(__file__).resolve().parents[3] / "app"
BASELINE = Path(__file__).with_name("org_context_baseline.json")
SPAWN = {"create_task", "ensure_future"}
G_OC1_EXEMPT = {"app/api/middlewares/auth.py", "app/core/envelope.py", "app/core/org_context.py"}
G_OC7_EXEMPT = {"app/core/org_context.py"}


def _scan() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {"detached_work": {}, "direct_org_reads": {}}
    for path in sorted(APP.rglob("*.py")):
        rel = "app/" + path.relative_to(APP).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:  # a stray non-module file is not service code
            continue
        spawn = reads = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in SPAWN:
                    spawn += 1
                if node.func.attr == "get" and node.args and ast.unparse(node.args[0]) in ("'orgId'", "'org_id'"):
                    reads += 1
            elif isinstance(node, ast.Subscript) and ast.unparse(node.slice) in ("'orgId'", "'org_id'"):
                reads += 1
        if spawn and rel not in G_OC7_EXEMPT:
            out["detached_work"][rel] = spawn
        if reads and rel not in G_OC1_EXEMPT:
            out["direct_org_reads"][rel] = reads
    return out


def _check(guard: str, hint: str) -> None:
    found = _scan()
    if os.environ.get("ORG_CONTEXT_UPDATE_BASELINE"):
        BASELINE.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n")
    base = json.loads(BASELINE.read_text())
    grown = {f: (base[guard].get(f, 0), n) for f, n in found[guard].items() if n > base[guard].get(f, 0)}
    assert not grown, f"{guard}: new sites (baseline, now) {grown}. {hint}"


def test_no_new_bare_task_creation() -> None:
    _check("detached_work", "Use app.core.org_context.spawn_detached (long-lived) or spawn_in_request (request child work).")


def test_no_new_direct_org_id_reads() -> None:
    _check("direct_org_reads", "Read the org from app.core.org_context.current() instead of claims or payloads.")
