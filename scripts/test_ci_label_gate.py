"""The heavy workflows run on a pull request only when it carries a `ci:` label (CONTRIBUTING.md, "Heavy CI").

Each job of a heavy workflow carries the same gate: anything but a pull request runs; on a pull
request, the label just added must be one of the workflow's labels (so an unrelated label does not
re-run it), or, on a push, the pull request must already carry one. A skipped job still reports,
so a required check never waits. The workflow keeps a schedule and *Run workflow*, so main is
still covered.

Run: python3 -m unittest discover -s scripts -p 'test_ci_label_gate.py'  (needs PyYAML)
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# Heavy workflow -> its area label (ci:full runs them all). A workflow missing from this checkout is skipped.
HEAVY = {
    "agent-harness-infrastructure.yml": "ci:harness",
    "harness-e2e.yml": "ci:harness",
    "harness-fault.yml": "ci:harness",
    "pg-runtime.yml": "ci:db",
    "pg-stores.yml": "ci:db",
    "pgvector-provider.yml": "ci:db",
    "db-migrations.yml": "ci:db",
    "docstore-migrate.yml": "ci:db",
    "permission-conformance.yml": "ci:db",
    "node-pg-runtime.yml": "ci:db",
    "postgres-image.yml": "ci:db",
    "backend-matrix.yml": "ci:db",
    "storage-backends.yml": "ci:db",
    "e2e-smoke.yml": "ci:e2e",
    "published-hub-smoke.yml": "ci:e2e",
    "arm64-smoke.yml": "ci:e2e",
    "electron-pr-build.yml": "ci:e2e",
    "native-bundle.yml": "ci:e2e",
    "sdk-tests.yml": "ci:e2e",
    "integration-tests.yml": "ci:e2e",
    "helm-chart.yml": "ci:e2e",
    "codeql.yml": "ci:security",
    "graph-explorer-tests.yml": "ci:full",
}
# Jobs of a heavy workflow that run without the gate: cheap jobs its gated jobs need, or a light job for forks.
UNGATED = {
    "integration-tests.yml": {"changes", "setup", "it-fork"},
    "helm-chart.yml": {"check"},
}
_ALLOWED = re.compile(r"github\.event\.action == 'labeled' && contains\(fromJSON\('(\[[^\]]*\])'\), github\.event\.label\.name\)")
_ACTED_ON = re.compile(r"github\.event\.action != 'unlabeled' \|\| contains\(fromJSON\('(\[[^\]]*\])'\), github\.event\.label\.name\)")
# A label the workflow's own conditions read, outside the gate (for example integration-tests' it-arango).
_READS = re.compile(r"(?:labels\.\*\.name, |label\.name == )'([^']+)'")


def _arr(labels: list[str]) -> str:
    return json.dumps(labels, separators=(",", ":"))


def _has_any(labels: list[str]) -> str:
    return " || ".join(f"contains(github.event.pull_request.labels.*.name, '{label}')" for label in labels)


def gate_terms(allowed: list[str], acted_on: list[str]) -> list[str]:
    """The pull request terms of the gate, as written in every heavy job.

    `allowed` start the job (just added, or already on the pull request); `acted_on` adds the labels whose
    removal re-runs it (allowed plus, say, skip-it). Only the label just added or removed counts on a
    label event, so an unrelated label re-runs nothing, and a removed label is never an opt-in: removal
    re-runs only while an allowed label is still on the pull request.
    """
    return [
        f"(github.event.action == 'labeled' && contains(fromJSON('{_arr(allowed)}'), github.event.label.name))",
        f"(github.event.action != 'labeled' && ({_has_any(allowed)}) &&"
        f" (github.event.action != 'unlabeled' || contains(fromJSON('{_arr(acted_on)}'), github.event.label.name)))",
    ]


def group_flag(allowed: list[str], acted_on: list[str]) -> str:
    """The concurrency group's suffix: 'heavy' for an event the gate acts on, 'other' for the rest.

    Runs that act cancel each other (a push cancels a run a label started); an unrelated label lands
    in 'other' and never cancels a running suite.
    """
    return (
        f"-${{{{ (github.event_name != 'pull_request' || contains(fromJSON('{_arr(acted_on)}'), github.event.label.name) || "
        f"(github.event.action != 'labeled' && github.event.action != 'unlabeled' && ({_has_any(allowed)}))) && 'heavy' || 'other' }}}}"
    )


def gate_problems(name: str, text: str, area: str | None = None, ungated: frozenset[str] | None = None) -> list[str]:
    """Problems with a gated workflow. With `area`, every job but `ungated` must carry the gate with that label."""
    workflow = yaml.safe_load(text)
    on = workflow.get(True, workflow.get("on")) or {}
    problems = []
    pr = on.get("pull_request") or {}
    if "labeled" not in (pr.get("types") or []):
        problems.append(f"{name}: pull_request types must include labeled, so adding a label starts the run")
    for trigger in ("schedule", "workflow_dispatch"):
        if trigger not in on:
            problems.append(f"{name}: no {trigger} trigger, so main is not covered without a label")
    allowed: list[str] = []
    acted_on: list[str] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        cond = " ".join(str(job.get("if", "")).split())
        m, n = _ALLOWED.search(cond), _ACTED_ON.search(cond)
        if not m:
            if area is not None and job_id not in (ungated or frozenset()):
                problems.append(f"{name}:{job_id}: a heavy job without the ci: label gate runs on every pull request")
            continue
        allowed, acted_on = json.loads(m.group(1)), json.loads(n.group(1)) if n else []
        if area is not None and area not in allowed:
            problems.append(f"{name}:{job_id}: its gate does not accept the workflow's area label {area}")
        if allowed[:1] != ["ci:full"]:
            problems.append(f"{name}:{job_id}: ci:full must come first in the gate's labels")
        if acted_on[: len(allowed)] != allowed:
            problems.append(f"{name}:{job_id}: the labels whose removal re-runs the job must start with the allowed ones")
        for term in gate_terms(allowed, acted_on):
            if term not in cond:
                problems.append(f"{name}:{job_id}: gate term missing or edited: {term}")
        if "github.event_name != 'pull_request' ||" not in cond:
            problems.append(f"{name}:{job_id}: the gate must let every other event through")
    if not allowed:
        problems.append(f"{name}: no job carries the ci: label gate")
        return problems
    unread = sorted({label for label in _READS.findall(text) if not label.startswith("ci:")} - set(acted_on))
    # A generated copy may add a start label of its own for its other jobs; its generator's tests cover that.
    if unread and not text.startswith("# Generated from"):
        problems.append(f"{name}: labels its jobs read are not in the gate, so adding or removing them skips the run: {unread}")
    group = ((workflow.get("concurrency") or {}).get("group") or "") if isinstance(workflow.get("concurrency"), dict) else ""
    if group and not group.endswith(group_flag(allowed, acted_on)):
        problems.append(f"{name}: the concurrency group must end with the gate's heavy/other flag")
    return problems


class CiLabelGateTest(unittest.TestCase):
    def test_heavy_workflows_are_gated(self) -> None:
        problems = []
        for name, area in HEAVY.items():
            path = WORKFLOWS / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            problems += gate_problems(name, text, area, frozenset(UNGATED.get(name, ())))
        self.assertEqual(problems, [])

    def test_every_gated_workflow_uses_the_same_gate(self) -> None:
        problems = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if "github.event.label.name" in text and '"ci:full"' in text:
                problems += gate_problems(path.name, text)
        self.assertEqual(problems, [])

    def test_an_ungated_job_is_caught(self) -> None:
        text = "on:\n  pull_request:\n    types: [labeled]\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
        ungated = text + "jobs:\n  a:\n    runs-on: ubuntu-latest\n"
        self.assertIn("x.yml: no job carries the ci: label gate", gate_problems("x.yml", ungated))
        allowed, acted_on = ["ci:full", "ci:db"], ["ci:full", "ci:db", "skip-x"]
        terms = gate_terms(allowed, acted_on)
        group = "concurrency:\n  group: x-${{ github.ref }}" + group_flag(allowed, acted_on) + "\n"
        good = text + group + f"jobs:\n  a:\n    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
        self.assertEqual(gate_problems("x.yml", good), [])
        # Re-running on any label event (labels already on the pull request) is the bug the gate prevents.
        loose = good.replace("(github.event.action != 'labeled' && (", "((")
        self.assertTrue(any("gate term missing" in p for p in gate_problems("x.yml", loose)))

    def _workflow(self, jobs: dict[str, list[str] | None]) -> str:
        text = "on:\n  pull_request:\n    types: [labeled]\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\njobs:\n"
        for job_id, allowed in jobs.items():
            text += f"  {job_id}:\n    runs-on: ubuntu-latest\n"
            if allowed:
                terms = gate_terms(allowed, allowed)
                text += f"    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
        return text

    def test_an_ungated_heavy_job_beside_a_gated_one_is_caught(self) -> None:
        text = self._workflow({"gated": ["ci:full", "ci:db"], "heavy": None, "light": None})
        problems = gate_problems("x.yml", text, "ci:db", frozenset({"light"}))
        self.assertEqual(problems, ["x.yml:heavy: a heavy job without the ci: label gate runs on every pull request"])

    def test_each_job_must_accept_the_area_label(self) -> None:
        text = self._workflow({"db": ["ci:full", "ci:db"], "e2e": ["ci:full", "ci:e2e"]})
        problems = gate_problems("x.yml", text, "ci:db")
        self.assertEqual(problems, ["x.yml:e2e: its gate does not accept the workflow's area label ci:db"])
        # A workflow's own label beside the area label is fine (harness-fault, it-all).
        self.assertEqual(gate_problems("x.yml", self._workflow({"a": ["ci:full", "ci:db", "db-wide"]}), "ci:db"), [])

    def test_a_removed_label_is_not_an_opt_in(self) -> None:
        good = self._workflow({"a": ["ci:full", "ci:db"]})
        self.assertEqual(gate_problems("x.yml", good), [])
        # Without the action check, removing the only ci:db label would start the job.
        loose = good.replace("(github.event.action == 'labeled' && contains(", "(contains(")
        self.assertTrue(any("gate term missing" in p or "no job carries" in p for p in gate_problems("x.yml", loose)))

    def test_label_and_push_runs_share_the_group(self) -> None:
        allowed = ["ci:full", "ci:e2e"]
        flagged = self._workflow({"a": allowed}).replace(
            "jobs:\n", "concurrency:\n  group: x-${{ github.event.pull_request.number }}" + group_flag(allowed, allowed) + "\njobs:\n"
        )
        self.assertEqual(gate_problems("x.yml", flagged), [])
        # A suffix that differs between the label event and the next push keeps the stale run going.
        for suffix in ("-${{ github.event.label.name }}", "-${{ github.event.action }}"):
            split = flagged.replace(group_flag(allowed, allowed), suffix)
            self.assertTrue(any("concurrency group" in p for p in gate_problems("x.yml", split)), suffix)

    def test_a_label_the_jobs_read_must_be_in_the_gate(self) -> None:
        allowed = ["ci:full", "ci:db"]
        terms = gate_terms(allowed, allowed)
        text = (
            "on:\n  pull_request:\n    types: [labeled]\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
            f"jobs:\n  a:\n    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
            "    env:\n      WIDE: ${{ contains(github.event.pull_request.labels.*.name, 'db-wide') }}\n"
        )
        self.assertTrue(any("db-wide" in p for p in gate_problems("x.yml", text)))

    def test_the_label_name_alone_must_not_split_the_group(self) -> None:
        allowed = ["ci:full", "ci:db"]
        terms = gate_terms(allowed, allowed)
        text = (
            "on:\n  pull_request:\n    types: [labeled]\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
            "concurrency:\n  group: x-${{ github.ref }}-${{ github.event.label.name }}\n"
            f"jobs:\n  a:\n    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
        )
        self.assertTrue(any("concurrency group" in p for p in gate_problems("x.yml", text)))


if __name__ == "__main__":
    unittest.main()
