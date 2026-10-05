"""Untrusted pull-request code must never share a job with repository secrets.

Stdlib only: main.yml runs `python3 -m unittest discover -s scripts` without
installing anything.
"""

import os
import re
import unittest
from pathlib import Path

REPO = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.y*ml"))

# Triggers that run with the base repo's secrets and a write-capable context
# while the event payload is attacker-influenced.
_PRIVILEGED_TRIGGER = re.compile(r"^\s{2}(pull_request_target|workflow_run)\s*:", re.M)
_PR_HEAD_REF = re.compile(
    r"github\.event\.pull_request\.head\.(sha|ref)|github\.head_ref|refs/pull/"
)
_SECRET = re.compile(r"\$\{\{[^}]*\bsecrets\.(?!GITHUB_TOKEN\b)[A-Z0-9_]+")

# A workflow listed here may keep a privileged trigger; it must never check out
# PR code. Add to it only with a security reviewer's sign-off. post-release-probe
# runs on the release workflow (tag pushes only) and checks out the default branch.
PRIVILEGED_TRIGGER_ALLOWLIST = {"post-release-probe.yml"}

_PULL_REQUEST_TRIGGER = re.compile(r"^\s{2}pull_request\s*:", re.M)
_SECRETS_INHERIT = re.compile(r"^\s+secrets:\s*inherit\s*$", re.M)
_FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
_JOBS_KEY = re.compile(r"^jobs:\s*$", re.M)
_JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$", re.M)
# The job-level `if:` plus any folded/continued lines indented deeper than it.
_JOB_IF = re.compile(r"^    if:(.*(?:\n {6,}.*)*)", re.M)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _jobs(text: str) -> dict[str, str]:
    jobs_key = _JOBS_KEY.search(text)
    if jobs_key is None:
        return {}
    body = text[jobs_key.end():]
    next_top_level = re.search(r"^\S", body, re.M)
    if next_top_level is not None:
        body = body[: next_top_level.start()]
    headers = list(_JOB_HEADER.finditer(body))
    return {
        h.group(1): body[h.end(): headers[i + 1].start() if i + 1 < len(headers) else len(body)]
        for i, h in enumerate(headers)
    }


def _unguarded_secret_jobs(text: str) -> list[str]:
    """Jobs of a pull_request workflow that can read secrets without their own fork guard."""
    if not _PULL_REQUEST_TRIGGER.search(text):
        return []
    jobs_key = _JOBS_KEY.search(text)
    # Secrets in workflow-level env reach every job.
    workflow_level_secret = bool(jobs_key and _SECRET.search(text[: jobs_key.start()]))
    offenders = []
    for name, block in _jobs(text).items():
        reads_secrets = workflow_level_secret or _SECRET.search(block) or _SECRETS_INHERIT.search(block)
        if not reads_secrets:
            continue
        condition = _JOB_IF.search(block)
        if condition is None or _FORK_GUARD not in condition.group(1):
            offenders.append(name)
    return offenders


class TestWorkflowTrust(unittest.TestCase):
    def test_workflows_found(self):
        self.assertTrue(WORKFLOWS, f"no workflows under {REPO}/.github/workflows; set REPO_ROOT")

    def test_no_job_bypasses_checkouts_pr_safety_guard(self) -> None:
        offenders = [p.name for p in WORKFLOWS if re.search(r"allow-unsafe-pr-checkout:\s*true", _text(p))]
        self.assertEqual(offenders, [], "allow-unsafe-pr-checkout: true runs PR code with secrets")

    def test_privileged_triggers_are_allowlisted(self) -> None:
        offenders = [
            p.name
            for p in WORKFLOWS
            if _PRIVILEGED_TRIGGER.search(_text(p)) and p.name not in PRIVILEGED_TRIGGER_ALLOWLIST
        ]
        self.assertEqual(offenders, [], "pull_request_target/workflow_run need a security sign-off")

    def test_privileged_workflows_never_check_out_pr_code(self) -> None:
        offenders = [
            p.name
            for p in WORKFLOWS
            if _PRIVILEGED_TRIGGER.search(_text(p)) and _PR_HEAD_REF.search(_text(p))
        ]
        self.assertEqual(offenders, [], "a privileged trigger checks out or references the PR head")

    def test_pull_request_jobs_with_secrets_skip_forks(self) -> None:
        # pull_request gives a fork no secrets anyway; this guards the self-hosted
        # runner and keeps the intent explicit: secrets only for same-repo heads.
        offenders = [f"{p.name}:{job}" for p in WORKFLOWS for job in _unguarded_secret_jobs(_text(p))]
        self.assertEqual(offenders, [], "pull_request job reads secrets without its own fork guard")


class TestUnguardedSecretJobs(unittest.TestCase):
    def test_a_guarded_job_does_not_cover_an_unguarded_one(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  guarded:\n"
            "    if: >-\n"
            "      github.event_name != 'pull_request' ||\n"
            "      github.event.pull_request.head.repo.full_name == github.repository\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  unguarded:\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
        )
        self.assertEqual(_unguarded_secret_jobs(workflow), ["unguarded"])

    def test_guard_in_a_step_condition_does_not_count(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  build:\n"
            "    steps:\n"
            "      - if: github.event.pull_request.head.repo.full_name == github.repository\n"
            "        run: echo ok\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
        )
        self.assertEqual(_unguarded_secret_jobs(workflow), ["build"])

    def test_workflow_level_secrets_and_inherited_secrets_count(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "env:\n"
            "  TOKEN: ${{ secrets.API_KEY }}\n"
            "jobs:\n"
            "  plain:\n"
            "    steps:\n"
            "      - run: echo hi\n"
            "  reusable:\n"
            "    uses: ./.github/workflows/x.yml\n"
            "    secrets: inherit\n"
        )
        self.assertEqual(_unguarded_secret_jobs(workflow), ["plain", "reusable"])

    def test_jobs_without_secrets_and_non_pull_request_workflows_pass(self) -> None:
        no_secrets = "on:\n  pull_request:\njobs:\n  lint:\n    steps:\n      - run: echo ${{ secrets.GITHUB_TOKEN }}\n"
        push_only = "on:\n  push:\njobs:\n  deploy:\n    steps:\n      - run: echo ${{ secrets.API_KEY }}\n"
        self.assertEqual(_unguarded_secret_jobs(no_secrets), [])
        self.assertEqual(_unguarded_secret_jobs(push_only), [])


if __name__ == "__main__":
    unittest.main()
