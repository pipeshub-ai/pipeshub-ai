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
# Keyed by the workflow's `name:`, so a copy of the workflow under another file name is covered too. They run on
# every event, an unrelated label included, so each run reports a real result rather than a skip (LABEL_EVENTS).
UNGATED = {
    "Integration Tests": {"changes", "setup", "it-fork"},
    "Helm chart": {"check"},
}
# The heavy-event predicate a cheap ungated job must not carry: skipping it on a label event would record a skip
# that replaces its last real result on the commit.
LABEL_EVENTS = "github.event.action != 'labeled' && github.event.action != 'unlabeled'"
_PR_CHECK = "github.event_name != 'pull_request'"
# A hand-written workflow whose labels are removed once they fire, so it starts on `labeled` only.
ONE_SHOT = "# ci: labels are one-shot here"
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
    another unrelated label's run). Anything else is
    `<event>-<ref>-<run id>`: a schedule, a manual run and a push never cancel each other or a pull request run.
    """
    return (
        f"github.event_name == 'pull_request' && format('pr-{{0}}-{{1}}', github.event.pull_request.number, "
        f"({heavy_flag(allowed, acted_on)}) && 'heavy' || format('other-{{0}}', github.run_id)) || "
        "format('{0}-{1}-{2}', github.event_name, github.ref, github.run_id)"
    )


def _merge(into: list[str], labels: list[str]) -> list[str]:
    return into + [label for label in labels if label not in into]


def _can_bypass(rest: str) -> bool:
    """Whether `gate && rest` could hold without the gate: a top-level `||`, or a `)` that closes the gate's
    group early (`false) || (true`), or parentheses or quotes left open."""
    depth, quoted = 0, False
    for i, c in enumerate(rest):
        if c == "'":
            quoted = not quoted
        elif not quoted and c in "()":
            depth += 1 if c == "(" else -1
            if depth < 0:
                return True
        elif not quoted and depth == 0 and rest.startswith("||", i):
            return True
    return depth != 0 or quoted


def gate_is_mandatory(cond: str, allowed: list[str], acted_on: list[str]) -> bool:
    """The condition is the gate alone, or the gate AND further restrictions: nothing can bypass it."""
    a, b = gate_terms(allowed, acted_on)
    gate = f"{_PR_CHECK} || {a} || {b}"
    if cond == gate:
        return True
    if cond.startswith(f"({gate}) && "):
        return not _can_bypass(cond[len(gate) + 6:])
    # The fork-guard form: the gate and the rest inside the pull request branch.
    head = f"{_PR_CHECK} || (({a} || {b}) && "
    if cond.startswith(head) and cond.endswith(")"):
        rest = cond[len(head):-1]
        return not _can_bypass(rest)
    return False


def gate_problems(name: str, text: str, area: str | None = None) -> list[str]:
    """Problems with a gated workflow. With `area`, every job not in UNGATED must carry the gate with that label."""
    workflow = yaml.safe_load(text)
    on = workflow.get(True, workflow.get("on")) or {}
    problems = []
    types = (on.get("pull_request") or {}).get("types") or []
    generated = text.startswith("# Generated from")
    if "labeled" not in types:
        problems.append(f"{name}: pull_request types must include labeled, so adding a label starts the run")
    # A push must re-run a labelled pull request's suite, unless the workflow says its labels are one-shot
    # (removed once they fire) or it is a generated copy whose generator chooses its events.
    if "synchronize" not in types and not generated and ONE_SHOT not in text:
        problems.append(f"{name}: pull_request types must include synchronize, so a push re-runs a labelled pull request")
    problems += [
        f"{name}: no {trigger} trigger, so main is not covered without a label"
        for trigger in ("schedule", "workflow_dispatch") if trigger not in on
    ]
    ungated = UNGATED.get(workflow.get("name"), set())
    allowed: list[str] = []
    acted_on: list[str] = []
    union_allowed: list[str] = []
    union_acted: list[str] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        if job_id in ungated:
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
        if set(acted_on) - set(allowed) and "unlabeled" not in types and not generated:
            problems.append(f"{name}:{job_id}: its gate re-runs on removing a label, so pull_request types must include unlabeled")
        problems += [
            f"{name}:{job_id}: gate term missing or edited: {term}" for term in gate_terms(allowed, acted_on) if term not in cond
        ]
        if not gate_is_mandatory(cond, allowed, acted_on):
            problems.append(f"{name}:{job_id}: the condition can run the job without the gate (it must be the gate, or the gate && more)")
        # Each job against its own lists: a label it reads must start it, or be one whose removal re-runs it
        # (removal-only, like skip-it, never an opt-in).
        reads = {label for label in _READS.findall(json.dumps(job)) if not label.startswith("ci:")}
        unread_here = sorted(reads - set(acted_on))
        if unread_here:
            problems.append(f"{name}:{job_id}: labels this job reads are not in its own gate: {unread_here}")
    if not allowed:
        problems.append(f"{name}: no job carries the ci: label gate")
        return problems
    unread = sorted({label for label in _READS.findall(text) if not label.startswith("ci:")} - set(union_acted))
    # A generated copy may add a start label of its own for its other jobs; its generator's tests cover that.
    if unread and not generated:
        problems.append(f"{name}: labels its jobs read are not in the gate, so adding or removing them skips the run: {unread}")
    for job_id in ungated:
        job = (workflow.get("jobs") or {}).get(job_id) or {}
        if LABEL_EVENTS in " ".join(str(job.get("if", "")).split()):
            problems.append(f"{name}:{job_id}: an ungated job must run on every event, not skip on a label event (LABEL_EVENTS)")
    concurrency = workflow.get("concurrency")
    group = str(concurrency.get("group") or "") if isinstance(concurrency, dict) else ""
    if not group and not generated:
        problems.append(f"{name}: no concurrency group, so a push does not supersede the run a label started")
    if group:
        m = _GROUP.fullmatch(group)
        # Built from every gated job's labels: a label only one job starts on must still be `heavy`.
        if not m or m.group(1) != group_key(union_allowed, union_acted):
            problems.append(f"{name}: the concurrency group must be <prefix>-${{{{ <the shared key> }}}} (group_key)")
    return problems


REQUIRED = WORKFLOWS.parent / "required-checks.txt"
_EVERY_PR = {"opened", "synchronize", "reopened"}


def listed_checks(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def required_check_problems(listed: list[str], workflows: dict[str, str]) -> list[str]:
    """A required check must report on every pull request, with no label.

    Branch protection matches check-run names, so each line is a job's check name: its `name:`, or its id when it
    has none. Matrix jobs and names built from an expression report under other names and never match. Every job
    with that name, in any workflow (copies included), must be in a workflow that starts on opened, synchronize
    and reopened (pull_request or pull_request_target) with no paths filter, and must not run only with a label.
    Heavy suites are opt-in, so no heavy workflow's job and no `*-result` may be listed.
    """
    problems = []
    jobs_by_check: dict[str, list[tuple[str, dict, dict]]] = {}
    heavy: set[str] = set()
    for file, text in workflows.items():
        workflow = yaml.safe_load(text) or {}
        for job_id, raw in (workflow.get("jobs") or {}).items():
            job = raw or {}
            check = str(job.get("name", job_id))
            if "${{" in check or (job.get("strategy") or {}).get("matrix"):
                continue
            jobs_by_check.setdefault(check, []).append((file, workflow, job))
            if '"ci:full"' in text:
                heavy.add(check)
    for check in listed:
        if check in heavy or check.endswith("-result"):
            problems.append(f"required-checks.txt: {check} is a job of a heavy suite, which runs only on request")
            continue
        if check not in jobs_by_check:
            problems.append(f"required-checks.txt: no job reports a check named {check} (use the job's name:, or its id)")
            continue
        for file, workflow, job in jobs_by_check[check]:
            on = workflow.get(True, workflow.get("on")) or {}
            triggers = [on[t] or {} for t in ("pull_request", "pull_request_target") if isinstance(on, dict) and t in on]
            if not any(_EVERY_PR <= set(t.get("types") or _EVERY_PR) for t in triggers):
                problems.append(f"required-checks.txt: {check} ({file}) does not start on every pull request push")
            if any("paths" in t or "paths-ignore" in t for t in triggers):
                problems.append(f"required-checks.txt: {check} ({file}) has a paths filter, so it stays pending on other changes")
            cond = str(job.get("if", ""))
            if "label.name" in cond or "labels.*.name" in cond:
                problems.append(f"required-checks.txt: {check} ({file}) runs only with a label")
    return problems


class CiLabelGateTest(unittest.TestCase):
    def test_the_required_checks_run_on_every_pull_request(self) -> None:
        workflows = {p.name: p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yml"))}
        listed = listed_checks(REQUIRED.read_text(encoding="utf-8"))
        self.assertTrue(listed)
        self.assertEqual(required_check_problems(listed, workflows), [])

    def test_a_check_that_can_stay_pending_is_not_required(self) -> None:
        fast = "name: Fast checks\non:\n  pull_request:\njobs:\n  lint:\n    name: Lint\n    runs-on: ubuntu-latest\n"
        self.assertEqual(required_check_problems(["Lint"], {"f.yml": fast}), [])
        # Branch protection matches the job's check name, not the workflow's name.
        self.assertTrue(required_check_problems(["Fast checks"], {"f.yml": fast}))
        by_id = fast.replace("    name: Lint\n", "")
        self.assertEqual(required_check_problems(["lint"], {"f.yml": by_id}), [])
        matrix = fast.replace("    runs-on:", "    strategy:\n      matrix:\n        os: [a, b]\n    runs-on:")
        self.assertTrue(required_check_problems(["Lint"], {"f.yml": matrix}))
        heavy = self._workflow({"stores": ["ci:full", "ci:db"]}, name="pg-stores")
        for listed in ("stores", "pg-stores-result"):
            self.assertTrue(required_check_problems([listed], {"x.yml": heavy}), listed)
        for bad in (
            fast.replace("  pull_request:\n", "  pull_request:\n    types: [labeled]\n"),
            fast.replace("  pull_request:\n", "  pull_request:\n    paths: ['docs/**']\n"),
            fast.replace("    runs-on:", "    if: github.event.label.name == 'run-checks'\n    runs-on:"),
        ):
            self.assertTrue(required_check_problems(["Lint"], {"f.yml": bad}), bad)
        # A copy of the job, under another file, that only a label starts.
        copy = fast.replace("  pull_request:\n", "  pull_request:\n    types: [labeled]\n")
        self.assertTrue(required_check_problems(["Lint"], {"f.yml": fast, "f-copy.yml": copy}))
        target = fast.replace("pull_request:", "pull_request_target:\n    types: [opened, synchronize, reopened]")
        self.assertEqual(required_check_problems(["Lint"], {"f.yml": target}), [])
        self.assertEqual(listed_checks("# comment\n\nLint\n"), ["Lint"])

    def test_an_ungated_job_never_skips_on_a_label_event(self) -> None:
        allowed = ["ci:full", "ci:e2e"]
        text = self._workflow({"install": allowed}, name="Helm chart").replace(
            "jobs:\n", "jobs:\n  check:\n    runs-on: ubuntu-latest\n", 1
        )
        self.assertEqual(gate_problems("x.yml", text), [])
        skipping = text.replace(
            "  check:\n", f"  check:\n    if: {_PR_CHECK} || ({LABEL_EVENTS}) || contains(fromJSON('{_arr(allowed)}'), github.event.label.name)\n", 1
        )
        self.assertTrue(any("must run on every event" in p for p in gate_problems("x.yml", skipping)))

    def test_heavy_workflows_are_gated(self) -> None:
        problems = []
        for name, area in HEAVY.items():
            path = WORKFLOWS / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            problems += gate_problems(name, text, area)
        self.assertEqual(problems, [])

    def test_every_gated_workflow_uses_the_same_gate(self) -> None:
        problems = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if "github.event.label.name" in text and '"ci:full"' in text:
                problems += gate_problems(path.name, text)
        self.assertEqual(problems, [])

    def _workflow(
        self, jobs: dict[str, list[str] | None], acted_on: list[str] | None = None, *,
        types: str = "[opened, synchronize, reopened, labeled, unlabeled]", name: str = "x", group: bool = True,
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
        self.assertFalse(any("without the ci: label gate" in p for p in gate_problems("x.yml", exempt, "ci:db")))

    def test_each_job_must_accept_the_area_label(self) -> None:
        text = self._workflow({"db": ["ci:full", "ci:db"], "e2e": ["ci:full", "ci:e2e"]})
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

    def test_a_rest_that_closes_the_gate_group_early_is_caught(self) -> None:
        good = self._workflow({"a": ["ci:full", "ci:db"]})
        t = gate_terms(["ci:full", "ci:db"], ["ci:full", "ci:db"])
        gate = f"{_PR_CHECK} || {t[0]} || {t[1]}"
        bad = good.replace(f"    if: {gate}\n", f"    if: {_PR_CHECK} || (({t[0]} || {t[1]}) && false) || (true)\n")
        self.assertTrue(any("without the gate" in p for p in gate_problems("x.yml", bad)))
        ok = good.replace(f"    if: {gate}\n", f"    if: {_PR_CHECK} || (({t[0]} || {t[1]}) && fork == 'ok' && (a || b))\n")
        self.assertEqual(gate_problems("x.yml", ok), [])

    def test_a_primary_workflow_needs_synchronize(self) -> None:
        labeled = self._workflow({"a": ["ci:full", "ci:db"]}, types="[labeled]")
        self.assertTrue(any("include synchronize" in p for p in gate_problems("x.yml", labeled)))
        # Explicit exceptions: a workflow that says its labels are one-shot, and a generated copy.
        self.assertEqual(gate_problems("x.yml", labeled.replace("on:\n", f"{ONE_SHOT}\non:\n", 1)), [])
        self.assertFalse(any("synchronize" in p for p in gate_problems("x.yml", "# Generated from x\n" + labeled)))

    def test_a_primary_workflow_needs_a_concurrency_group(self) -> None:
        bare = self._workflow({"a": ["ci:full", "ci:db"]}, group=False)
        self.assertTrue(any("no concurrency group" in p for p in gate_problems("x.yml", bare)))

    def test_each_job_reads_only_its_own_labels(self) -> None:
        # Job a reads db-wide but does not start on it; job b starting on it must not hide that.
        jobs = {"a": ["ci:full", "ci:db"], "b": ["ci:full", "ci:db", "db-wide"]}
        text = self._workflow(jobs).replace(
            "  b:\n", "    env:\n      WIDE: ${{ contains(github.event.pull_request.labels.*.name, 'db-wide') }}\n  b:\n", 1
        )
        self.assertIn("x.yml:a: labels this job reads are not in its own gate: ['db-wide']", gate_problems("x.yml", text))
        # A removal-only label (re-runs on removal, never an opt-in) may be read: skip-it in integration-tests.
        skip = self._workflow({"a": ["ci:full", "ci:db"]}, acted_on=["skip-x"]).replace(
            "jobs:\n  a:\n", "jobs:\n  a:\n    env:\n      SKIP: ${{ contains(github.event.pull_request.labels.*.name, 'skip-x') }}\n", 1
        )
        self.assertFalse(any("skip-x" in p for p in gate_problems("x.yml", skip)))

    def test_a_label_the_jobs_read_must_be_in_the_gate(self) -> None:
        text = self._workflow({"a": ["ci:full", "ci:db"]}) + (
            "    env:\n      WIDE: ${{ contains(github.event.pull_request.labels.*.name, 'db-wide') }}\n"
        )
        self.assertTrue(any("db-wide" in p for p in gate_problems("x.yml", text)))


if __name__ == "__main__":
    unittest.main()
