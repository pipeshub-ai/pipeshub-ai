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


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
        fork_guard = "github.event.pull_request.head.repo.full_name == github.repository"
        offenders = []
        for p in WORKFLOWS:
            text = _text(p)
            if re.search(r"^\s{2}pull_request\s*:", text, re.M) and _SECRET.search(text) and fork_guard not in text:
                offenders.append(p.name)
        self.assertEqual(offenders, [], "pull_request workflow reads secrets without a fork guard")


if __name__ == "__main__":
    unittest.main()
