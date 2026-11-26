"""SEC-D-12: each workflow-privilege rule fails on a seeded bad workflow and passes on this repository.

Run: python3 -m unittest discover -s scripts -p 'test_workflow_lint.py'  (needs PyYAML)
backend/python/tests/unit/harness/security/test_sec_d_12_workflows.py collects these cases into the security gate.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import workflow_lint as wl  # noqa: E402

REAL_REPO = Path(__file__).resolve().parent.parent
SHA = "11d5960a326750d5838078e36cf38b85af677262"
OWNERS = "".join(f"{p} @org/maintainers\n" for p in wl.OWNED_PATHS)

GOOD = textwrap.dedent(f"""\
    name: CI
    on: [pull_request]
    permissions:
      contents: read
    jobs:
      test:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@{SHA} # v4
            with:
              persist-credentials: false
          - name: Uses an input through env
            env:
              TITLE: ${{{{ github.event.pull_request.title }}}}
              N: ${{{{ github.event.pull_request.number }}}}
            run: |
              echo "$TITLE" "${{{{ github.event.pull_request.number }}}}" "${{{{ github.sha }}}}"
    """)


class LintCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="pxh-wl-")
        self.root = Path(self._tmp.name)
        self.write(".github/CODEOWNERS", OWNERS)
        self.write(".github/workflows/ci.yml", GOOD)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def workflow(self, text: str, name: str = "x.yml") -> None:
        self.write(f".github/workflows/{name}", textwrap.dedent(text))

    def findings(self) -> list[wl.Finding]:
        return wl.run_checks(self.root)

    def assertOnly(self, rule: str) -> list[wl.Finding]:  # noqa: N802 - unittest naming
        found = self.findings()
        self.assertTrue(found, f"expected a {rule} finding")
        self.assertEqual({f.rule for f in found}, {rule}, "\n".join(map(str, found)))
        return found


class TestFixture(LintCase):
    def test_sec_d_12_the_fixture_passes(self) -> None:
        self.assertEqual(self.findings(), [])


class TestPermissions(LintCase):
    def test_sec_d_12_a_workflow_without_permissions_fails(self) -> None:
        self.workflow("on: push\njobs:\n  a:\n    runs-on: x\n    steps: [{run: echo}]\n")
        self.assertIn("set top-level `permissions:`", self.assertOnly("WF-PERMS")[0].message)

    def test_sec_d_12_a_write_or_everything_at_the_top_fails(self) -> None:
        for perms in ("permissions: write-all", "permissions: read-all", "permissions:\n  contents: write",
                      "permissions:\n  contents: read\n  issues: write"):
            with self.subTest(perms=perms):
                self.workflow(f"on: push\n{perms}\njobs:\n  a:\n    runs-on: x\n    steps: [{{run: echo}}]\n")
                self.assertOnly("WF-PERMS")

    def test_sec_d_12_a_write_on_the_job_that_needs_it_passes(self) -> None:
        self.workflow("on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    permissions:\n      issues: write\n"
                      "    steps: [{run: echo}]\n")
        self.assertEqual(self.findings(), [])


class TestPersistedCredentials(LintCase):
    def test_sec_d_12_a_checkout_that_keeps_the_token_fails(self) -> None:
        for with_ in ("", "\n        with:\n          ref: main", "\n        with:\n          persist-credentials: true"):
            with self.subTest(with_=with_):
                self.workflow(f"on: push\npermissions: {{}}\njobs:\n  a:\n    runs-on: x\n    steps:\n"
                              f"      - uses: actions/checkout@{SHA}{with_}\n")
                self.assertOnly("WF-PERSIST")

    def test_sec_d_12_an_excused_job_passes_and_an_unused_excuse_fails(self) -> None:
        self.workflow(f"on: push\npermissions: {{}}\njobs:\n  push:\n    runs-on: x\n    steps:\n"
                      f"      - uses: actions/checkout@{SHA}\n")
        self.write(str(wl.EXCEPTIONS), json.dumps({"exceptions": [
            {"rule": "WF-PERSIST", "workflow": "x.yml", "job": "push", "reason": "pushes"}]}))
        self.assertEqual(self.findings(), [])
        self.workflow(f"on: push\npermissions: {{}}\njobs:\n  push:\n    runs-on: x\n    steps:\n"
                      f"      - uses: actions/checkout@{SHA}\n        with:\n          persist-credentials: false\n")
        self.assertIn("needs no excuse", self.assertOnly("WF-PERSIST")[0].message)


class TestExpressions(LintCase):
    def run_line(self, line: str) -> list[wl.Finding]:
        self.workflow("on: workflow_dispatch\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    steps:\n"
                      f"      - run: |\n          {line}\n")
        return self.findings()

    def test_sec_d_12_an_attacker_writable_expression_in_a_script_fails(self) -> None:
        for expr in ("inputs.version", "github.event.inputs.stacks", "github.head_ref",
                     "github.event.pull_request.title", "github.event.pull_request.body",
                     "github.event.pull_request.head.ref", "github.event.issue.title", "github.event.comment.body",
                     "github.event.release.body", "github.event.release.tag_name", "github.event.head_commit.message",
                     "github.event.commits[0].message", "github.event.review.body",
                     "inputs.python-coverage-threshold || 89.8", "format('{0}', inputs.x)",
                     "toJSON(github.event.pull_request)", "github.event.pull_request.labels.*.name"):
            with self.subTest(expr=expr):
                found = self.run_line(f'echo "${{{{ {expr} }}}}"')
                self.assertEqual({f.rule for f in found}, {"WF-EXPR"}, "\n".join(map(str, found)))

    def test_sec_d_12_the_expression_is_caught_however_it_is_spaced_or_placed(self) -> None:
        for line in ('v="${{inputs.version}}"', 'v=${{   inputs.version   }}', "if [ x = '${{ inputs.v }}' ]; then :; fi",
                     'X=$(echo ${{ github.event.inputs.x }} | tr a b)'):
            with self.subTest(line=line):
                self.assertEqual({f.rule for f in self.run_line(line)}, {"WF-EXPR"})

    def test_sec_d_12_numbers_shas_and_env_are_not_text(self) -> None:
        for expr in ("github.event.pull_request.number", "github.event.pull_request.head.sha", "github.sha",
                     "github.run_id", "matrix.variant", "env.IMAGE", "steps.v.outputs.version",
                     "needs.resolve.outputs.candidate", "secrets.X != ''", "github.event_name"):
            with self.subTest(expr=expr):
                self.assertEqual(self.run_line(f'echo "${{{{ {expr} }}}}"'), [])

    def test_sec_d_12_a_github_script_body_is_a_script_too(self) -> None:
        self.workflow("on: workflow_dispatch\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    steps:\n"
                      f"      - uses: actions/github-script@{SHA}\n        with:\n"
                      "          script: core.info('${{ github.event.issue.body }}')\n")
        self.assertOnly("WF-EXPR")

    def test_sec_d_12_an_input_in_env_or_with_passes(self) -> None:
        self.workflow("on: workflow_dispatch\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    steps:\n"
                      "      - env:\n          V: ${{ inputs.version }}\n        run: echo \"$V\"\n"
                      f"      - uses: actions/checkout@{SHA}\n        with:\n          ref: ${{{{ inputs.ref }}}}\n"
                      "          persist-credentials: false\n")
        self.assertEqual(self.findings(), [])


class TestAgent(LintCase):
    def agent(self, tools: str, extra_env: str = "", perms: str = "contents: read") -> None:
        self.write(".github/workflows/x.yml",
                   "on: workflow_dispatch\npermissions: {}\njobs:\n  gen:\n    runs-on: x\n"
                   f"    permissions:\n      {perms}\n    steps:\n"
                   f"      - uses: anthropics/claude-code-action@{SHA}\n{extra_env}"
                   f"        with:\n          claude_args: '--model m --allowedTools \"{tools}\"'\n")

    def test_sec_d_12_an_agent_with_a_shell_or_web_tool_fails(self) -> None:
        for tools in ("Read,Bash(python3:*)", "Read,Bash", "Read,Bash(ls:*)", "Read,WebFetch", "Read,WebSearch",
                      "Read,mcp__github__create_issue", "Read,Task", "*", "Read,NotebookEdit"):
            with self.subTest(tools=tools):
                self.agent(tools)
                self.assertOnly("WF-AGENT")

    def test_sec_d_12_an_agent_with_a_token_in_its_environment_fails(self) -> None:
        for var in ("GH_TOKEN", "GITHUB_TOKEN", "DOCS_REPO_TOKEN", "MY_PAT"):
            with self.subTest(var=var):
                self.agent("Read,Write", extra_env=f"        env:\n          {var}: x\n")
                self.assertOnly("WF-AGENT")

    def test_sec_d_12_an_agent_in_a_job_that_can_write_fails(self) -> None:
        self.agent("Read,Write", perms="contents: write")
        self.assertIn("write permissions", self.assertOnly("WF-AGENT")[0].message)

    def test_sec_d_12_an_agent_with_no_tool_list_fails(self) -> None:
        self.workflow(f"on: push\npermissions: {{}}\njobs:\n  a:\n    runs-on: x\n    steps:\n"
                      f"      - uses: anthropics/claude-code-action@{SHA}\n        with:\n          prompt: hi\n")
        self.assertIn("name the agent's tools", self.assertOnly("WF-AGENT")[0].message)

    def test_sec_d_12_an_agent_with_file_tools_only_passes(self) -> None:
        self.agent("Read,Write,Edit,Glob,Grep,Skill")
        self.assertEqual(self.findings(), [])


class TestTriggersAndOwners(LintCase):
    def test_sec_d_12_a_trigger_that_runs_secrets_on_outsider_input_fails(self) -> None:
        for trigger in wl.UNSAFE_TRIGGERS:
            with self.subTest(trigger=trigger):
                self.workflow(f"on: [{trigger}]\npermissions: {{}}\njobs: {{}}\n")
                self.assertOnly("WF-TRIGGER")

    def test_sec_d_12_workflow_run_may_follow_only_a_workflow_a_pr_cannot_start(self) -> None:
        self.workflow("name: Release\non:\n  push:\n    tags: ['v*']\npermissions: {}\njobs: {}\n", "rel.yml")
        self.workflow("on:\n  workflow_run:\n    workflows: [Release, CI]\n    types: [completed]\npermissions: {}\n"
                      "jobs: {}\n")
        self.assertIn("'CI'", self.assertOnly("WF-TRIGGER")[0].message)
        self.workflow("on:\n  workflow_run:\n    workflows: [Release]\npermissions: {}\njobs: {}\n")
        self.assertEqual(self.findings(), [])

    def test_sec_d_12_codeowners_must_cover_what_ci_and_installs_trust(self) -> None:
        (self.root / ".github/CODEOWNERS").unlink()
        self.assertIn("missing", self.assertOnly("WF-OWNERS")[0].message)
        self.write(".github/CODEOWNERS", OWNERS.replace("/contracts/supply-chain/ @org/maintainers\n", "")
                   + "# /contracts/supply-chain/ @org/maintainers\n/deployment/\n")
        found = self.assertOnly("WF-OWNERS")
        self.assertEqual(sorted(f.message for f in found),
                         ["/contracts/supply-chain/ has no owner", "/deployment/ has no owner"])


class TestThisRepository(unittest.TestCase):
    def test_sec_d_12_this_repository_passes_every_rule(self) -> None:
        found = wl.run_checks(REAL_REPO)
        self.assertEqual(found, [], "\n".join(map(str, found)))


if __name__ == "__main__":
    unittest.main()
