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
from typing import Any

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
# Keyed by the workflow's `name:`, so a copy of the workflow under another file name is covered too.
UNGATED = {
    "Integration Tests": {"changes", "setup", "it-fork"},
    "Helm chart": {"check"},
}
_PR_CHECK = "github.event_name != 'pull_request'"
_GROUP = re.compile(r"[A-Za-z0-9_.-]+-\$\{\{ (.*) \}\}")
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


def heavy_flag(allowed: list[str], acted_on: list[str]) -> str:
    """True for a pull request event the gate acts on: a label it acts on, or a push with an allowed label on."""
    return (
        f"contains(fromJSON('{_arr(acted_on)}'), github.event.label.name) || "
        f"(github.event.action != 'labeled' && github.event.action != 'unlabeled' && ({_has_any(allowed)}))"
    )


def group_key(allowed: list[str], acted_on: list[str]) -> str:
    """The concurrency group after its static prefix, the same in every heavy workflow.

    A pull request is `pr-<number>-heavy` for an event the gate acts on, so a push cancels a run a label
    started, and `pr-<number>-other-<run id>` for the rest, so an unrelated label never cancels a run (nor
    another unrelated label's run, whose result check is waiting on an earlier run). Anything else is
    `<event>-<ref>-<run id>`: a schedule, a manual run and a push never cancel each other or a pull request run.
    """
    return (
        f"github.event_name == 'pull_request' && format('pr-{{0}}-{{1}}', github.event.pull_request.number, "
        f"({heavy_flag(allowed, acted_on)}) && 'heavy' || format('other-{{0}}', github.run_id)) || "
        "format('{0}-{1}-{2}', github.event_name, github.ref, github.run_id)"
    )


def _merge(into: list[str], labels: list[str]) -> list[str]:
    return into + [label for label in labels if label not in into]


def _result_job_id(jobs: dict[str, Any]) -> str | None:
    """The job that uses ./.github/actions/heavy-result (a copy of the workflow may live under another file name)."""
    for job_id, job in jobs.items():
        steps = job.get("steps") if isinstance(job, dict) else None
        if any(str(st.get("uses", "")).startswith("./.github/actions/heavy-result") for st in steps or []):
            return job_id
    return None


def result_problems(name: str, stem: str, jobs: dict[str, Any], allowed: list[str], acted_on: list[str]) -> list[str]:
    """The `<workflow>-result` job: needs every other job, always runs, and gets the workflow's heavy flag.

    An unrelated label starts a run whose heavy jobs skip, and a skip reports success; this job instead
    reports the latest earlier result on the commit (scripts/heavy_result.py), so branch protection
    requires it rather than the heavy jobs.
    """
    rid = f"{stem}-result" if stem else (_result_job_id(jobs) or "<workflow>-result")
    job = jobs.get(rid)
    if not isinstance(job, dict):
        return [f"{name}: no {rid} job, so an unrelated label's skipped run could replace a failed result"]
    problems = []
    needs = job.get("needs") or []
    missing = sorted(set(jobs) - {rid} - set(needs if isinstance(needs, list) else [needs]))
    if missing:
        problems.append(f"{name}:{rid}: must need every other job, missing {missing}")
    if str(job.get("if", "")).strip() != "always()":
        problems.append(f"{name}:{rid}: must run with if: always()")
    if job.get("name") != rid:
        problems.append(f"{name}:{rid}: its check name must be {rid}")
    steps = job.get("steps") or []
    use = next((st for st in steps if str(st.get("uses", "")).startswith("./.github/actions/heavy-result")), None)
    if use is None:
        return problems + [f"{name}:{rid}: must use ./.github/actions/heavy-result"]
    inputs = use.get("with") or {}
    want = f"${{{{ github.event_name != 'pull_request' || {heavy_flag(allowed, acted_on)} }}}}"
    if " ".join(str(inputs.get("heavy", "")).split()) != want:
        problems.append(f"{name}:{rid}: its heavy input must be the workflow's heavy flag")
    if inputs.get("needs") != "${{ toJSON(needs) }}" or inputs.get("job") != rid:
        problems.append(f"{name}:{rid}: must pass toJSON(needs) and its own name")
    return problems


def _has_top_level_or(cond: str) -> bool:
    depth, quoted = 0, False
    for i, c in enumerate(cond):
        if c == "'":
            quoted = not quoted
        elif not quoted and c in "()":
            depth += 1 if c == "(" else -1
        elif not quoted and depth == 0 and cond.startswith("||", i):
            return True
    return False


def gate_is_mandatory(cond: str, allowed: list[str], acted_on: list[str]) -> bool:
    """The condition is the gate alone, or the gate AND further restrictions: nothing can bypass it."""
    a, b = gate_terms(allowed, acted_on)
    gate = f"{_PR_CHECK} || {a} || {b}"
    if cond == gate:
        return True
    if cond.startswith(f"({gate}) && "):
        return not _has_top_level_or(cond[len(gate) + 6:])
    # The fork-guard form: the gate and the rest inside the pull request branch.
    head = f"{_PR_CHECK} || (({a} || {b}) && "
    if cond.startswith(head) and cond.endswith(")"):
        rest = cond[len(head):-1]
        return rest.count("(") == rest.count(")") and not _has_top_level_or(rest)
    return False


def gate_problems(name: str, text: str, area: str | None = None, stem: str | None = None) -> list[str]:
    """Problems with a gated workflow. With `area`, every job not in UNGATED must carry the gate with that label."""
    workflow = yaml.safe_load(text)
    on = workflow.get(True, workflow.get("on")) or {}
    problems = []
    types = (on.get("pull_request") or {}).get("types") or []
    if "labeled" not in types:
        problems.append(f"{name}: pull_request types must include labeled, so adding a label starts the run")
    for trigger in ("schedule", "workflow_dispatch"):
        if trigger not in on:
            problems.append(f"{name}: no {trigger} trigger, so main is not covered without a label")
    ungated = UNGATED.get(workflow.get("name"), set())
    allowed: list[str] = []
    acted_on: list[str] = []
    union_allowed: list[str] = []
    union_acted: list[str] = []
    rid = f"{stem}-result" if stem else _result_job_id(workflow.get("jobs") or {})
    for job_id, job in (workflow.get("jobs") or {}).items():
        if job_id in ungated or job_id == rid:
            continue
        cond = " ".join(str(job.get("if", "")).split())
        m, n = _ALLOWED.search(cond), _ACTED_ON.search(cond)
        if not m:
            if area is not None:
                problems.append(f"{name}:{job_id}: a heavy job without the ci: label gate runs on every pull request")
            continue
        allowed, acted_on = json.loads(m.group(1)), json.loads(n.group(1)) if n else []
        union_allowed, union_acted = _merge(union_allowed, allowed), _merge(union_acted, acted_on)
        if area is not None and area not in allowed:
            problems.append(f"{name}:{job_id}: its gate does not accept the workflow's area label {area}")
        if allowed[:1] != ["ci:full"]:
            problems.append(f"{name}:{job_id}: ci:full must come first in the gate's labels")
        if acted_on[: len(allowed)] != allowed:
            problems.append(f"{name}:{job_id}: the labels whose removal re-runs the job must start with the allowed ones")
        # A generated copy's triggers are its generator's (which may start it on fewer events); its tests cover that.
        if set(acted_on) - set(allowed) and "unlabeled" not in types and not text.startswith("# Generated from"):
            problems.append(f"{name}:{job_id}: its gate re-runs on removing a label, so pull_request types must include unlabeled")
        for term in gate_terms(allowed, acted_on):
            if term not in cond:
                problems.append(f"{name}:{job_id}: gate term missing or edited: {term}")
        if not gate_is_mandatory(cond, allowed, acted_on):
            problems.append(f"{name}:{job_id}: the condition can run the job without the gate (it must be the gate, or the gate && more)")
    if not allowed:
        problems.append(f"{name}: no job carries the ci: label gate")
        return problems
    unread = sorted({label for label in _READS.findall(text) if not label.startswith("ci:")} - set(union_acted))
    # A generated copy may add a start label of its own for its other jobs; its generator's tests cover that.
    if unread and not text.startswith("# Generated from"):
        problems.append(f"{name}: labels its jobs read are not in the gate, so adding or removing them skips the run: {unread}")
    concurrency = workflow.get("concurrency")
    group = str(concurrency.get("group") or "") if isinstance(concurrency, dict) else ""
    if group:
        m = _GROUP.fullmatch(group)
        # Built from every gated job's labels: a label only one job starts on must still be `heavy`.
        if not m or m.group(1) != group_key(union_allowed, union_acted):
            problems.append(f"{name}: the concurrency group must be <prefix>-${{{{ <the shared key> }}}} (group_key)")
    problems += result_problems(name, stem or "", workflow.get("jobs") or {}, union_allowed, union_acted)
    return problems


class CiLabelGateTest(unittest.TestCase):
    def test_heavy_workflows_are_gated(self) -> None:
        problems = []
        for name, area in HEAVY.items():
            path = WORKFLOWS / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            problems += gate_problems(name, text, area, name[: -len(".yml")])
        self.assertEqual(problems, [])

    def test_every_gated_workflow_uses_the_same_gate(self) -> None:
        problems = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if "github.event.label.name" in text and '"ci:full"' in text:
                problems += gate_problems(path.name, text)
        self.assertEqual(problems, [])

    def _workflow(
        self, jobs: dict[str, list[str] | None], acted_on: list[str] | None = None,
        types: str = "[labeled, unlabeled]", name: str = "x", group: bool = True, result: bool = True,
    ) -> str:
        text = (
            f"name: {name}\non:\n  pull_request:\n    types: {types}\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
        )
        allowed_all: list[str] = []
        for labels in jobs.values():
            allowed_all = _merge(allowed_all, labels or [])
        acted_all = _merge(allowed_all, acted_on or [])
        if group and allowed_all:
            text += f"concurrency:\n  group: x-${{{{ {group_key(allowed_all, acted_all)} }}}}\n"
        text += "jobs:\n"
        for job_id, allowed in jobs.items():
            text += f"  {job_id}:\n    runs-on: ubuntu-latest\n"
            if allowed:
                t = gate_terms(allowed, _merge(allowed, acted_on or []))
                text += f"    if: {_PR_CHECK} || {t[0]} || {t[1]}\n"
        if result:
            text += (
                f"  x-result:\n    name: x-result\n    needs: [{', '.join(jobs)}]\n    if: always()\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - uses: ./.github/actions/heavy-result\n        with:\n"
                f"          heavy: ${{{{ {_PR_CHECK} || {heavy_flag(allowed_all, acted_all)} }}}}\n"
                "          needs: ${{ toJSON(needs) }}\n          job: x-result\n"
            )
        return text

    def test_a_good_workflow_passes(self) -> None:
        self.assertEqual(gate_problems("x.yml", self._workflow({"a": ["ci:full", "ci:db"]}), "ci:db"), [])

    def test_an_ungated_job_is_caught(self) -> None:
        text = self._workflow({"a": None})
        self.assertIn("x.yml: no job carries the ci: label gate", gate_problems("x.yml", text))
        # Re-running on any label event (labels already on the pull request) is the bug the gate prevents.
        loose = self._workflow({"a": ["ci:full", "ci:db"]}).replace("(github.event.action != 'labeled' && (", "((")
        self.assertTrue(any("gate term missing" in p for p in gate_problems("x.yml", loose)))

    def test_an_ungated_heavy_job_beside_a_gated_one_is_caught(self) -> None:
        text = self._workflow({"gated": ["ci:full", "ci:db"], "heavy": None})
        self.assertEqual(
            gate_problems("x.yml", text, "ci:db"),
            ["x.yml:heavy: a heavy job without the ci: label gate runs on every pull request"],
        )
        # An UNGATED job (by workflow name) is not reported.
        exempt = self._workflow({"gated": ["ci:full", "ci:db"], "check": None}, name="Helm chart")
        self.assertEqual(gate_problems("x.yml", exempt, "ci:db"), [])

    def test_each_job_must_accept_the_area_label(self) -> None:
        text = self._workflow({"db": ["ci:full", "ci:db"], "e2e": ["ci:full", "ci:e2e"]}, group=False)
        self.assertEqual(
            gate_problems("x.yml", text, "ci:db"),
            ["x.yml:e2e: its gate does not accept the workflow's area label ci:db"],
        )
        # A workflow's own label beside the area label is fine (harness-fault, it-all).
        self.assertEqual(gate_problems("x.yml", self._workflow({"a": ["ci:full", "ci:db", "db-wide"]}), "ci:db"), [])

    def test_a_removed_label_is_not_an_opt_in(self) -> None:
        good = self._workflow({"a": ["ci:full", "ci:db"]})
        # Without the action check, removing the only ci:db label would start the job.
        loose = good.replace("(github.event.action == 'labeled' && contains(", "(contains(")
        self.assertTrue(any("gate term missing" in p or "no job carries" in p for p in gate_problems("x.yml", loose)))

    def test_a_removal_aware_gate_needs_the_unlabeled_trigger(self) -> None:
        jobs = {"a": ["ci:full", "ci:db"]}
        acted_on = ["ci:full", "ci:db", "skip-x"]
        self.assertEqual(gate_problems("x.yml", self._workflow(jobs, acted_on)), [])
        labeled_only = self._workflow(jobs, acted_on, types="[labeled]")
        self.assertTrue(any("must include unlabeled" in p for p in gate_problems("x.yml", labeled_only)))

    def test_a_condition_that_bypasses_the_gate_is_caught(self) -> None:
        good = self._workflow({"a": ["ci:full", "ci:db"]})
        t = gate_terms(["ci:full", "ci:db"], ["ci:full", "ci:db"])
        gate = f"{_PR_CHECK} || {t[0]} || {t[1]}"
        for bypass in (f"true || {gate}", f"({gate}) && x == 'y' || true", f"{_PR_CHECK} || (({t[0]} || {t[1]}) && x || true)"):
            bad = good.replace(f"    if: {gate}\n", f"    if: {bypass}\n")
            self.assertTrue(any("without the gate" in p for p in gate_problems("x.yml", bad)), bypass)
        # Further restrictions that cannot bypass it are fine, in both forms the workflows use.
        for narrowed in (f"({gate}) && x == 'y' && (a || b)", f"{_PR_CHECK} || (({t[0]} || {t[1]}) && fork == 'ok' && (a || b))"):
            ok = good.replace(f"    if: {gate}\n", f"    if: {narrowed}\n")
            self.assertEqual(gate_problems("x.yml", ok), [], narrowed)

    def test_the_group_has_the_shared_shape(self) -> None:
        good = self._workflow({"a": ["ci:full", "ci:e2e"]})
        key = group_key(["ci:full", "ci:e2e"], ["ci:full", "ci:e2e"])
        self.assertIn(f"  group: x-${{{{ {key} }}}}\n", good)
        bad_groups = (
            # A label-name split: a label event and the next push do not share a group.
            "x-${{ github.event.pull_request.number }}-${{ github.event.label.name }}",
            # The right key behind a label-dependent prefix still splits by label.
            f"x-${{{{ github.event.label.name }}}}-${{{{ {key} }}}}",
            # Schedule and Run workflow on main share `refs/heads/main` and cancel each other.
            "x-${{ github.ref }}",
            "x-${{ github.event.pull_request.number }}",
        )
        for bad in bad_groups:
            text = good.replace(f"x-${{{{ {key} }}}}", bad)
            self.assertTrue(any("concurrency group" in p for p in gate_problems("x.yml", text)), bad)

    def test_the_group_is_built_from_every_gated_job(self) -> None:
        jobs = {"a": ["ci:full", "ci:db", "db-wide"], "b": ["ci:full", "ci:db"]}
        good = self._workflow(jobs)
        self.assertEqual(gate_problems("x.yml", good), [])
        # Built from the last job only, adding db-wide would start job a in an `other` group.
        last_only = group_key(["ci:full", "ci:db"], ["ci:full", "ci:db"])
        union = group_key(["ci:full", "ci:db", "db-wide"], ["ci:full", "ci:db", "db-wide"])
        bad = good.replace(union, last_only)
        self.assertNotEqual(bad, good)
        self.assertTrue(any("concurrency group" in p for p in gate_problems("x.yml", bad)))

    def test_an_unrelated_label_cannot_replace_a_failure(self) -> None:
        good = self._workflow({"a": ["ci:full", "ci:db"], "lint": None}, name="Helm chart")
        missing = self._workflow({"a": ["ci:full", "ci:db"]}, result=False)
        self.assertTrue(any("-result job, so an unrelated label" in p for p in gate_problems("x.yml", missing)))
        cases = {
            "if: always()": ("    if: always()\n", "    if: success()\n"),
            "need every other job": ("    needs: [a, lint]\n", "    needs: [a]\n"),
            "heavy flag": ("          heavy: ${{ github.event_name != 'pull_request' || ", "          heavy: ${{ "),
            "toJSON(needs)": ("          needs: ${{ toJSON(needs) }}\n", "          needs: '{}'\n"),
        }
        for want, (old, new) in cases.items():
            bad = good.replace(old, new)
            self.assertNotEqual(bad, good, want)
            self.assertTrue(any(want in p for p in gate_problems("x.yml", bad)), want)

    def test_a_label_the_jobs_read_must_be_in_the_gate(self) -> None:
        text = self._workflow({"a": ["ci:full", "ci:db"]}) + (
            "    env:\n      WIDE: ${{ contains(github.event.pull_request.labels.*.name, 'db-wide') }}\n"
        )
        self.assertTrue(any("db-wide" in p for p in gate_problems("x.yml", text)))


if __name__ == "__main__":
    unittest.main()
