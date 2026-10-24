"""Untrusted pull-request code must never share a job with repository secrets.

Stdlib only: main.yml runs `python3 -m unittest discover -s scripts` without
installing anything.
"""

import hashlib
import os
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.y*ml"))

# Triggers that run with the base repo's secrets and a write-capable context
# while the event payload is attacker-influenced.
def _trigger(events: str) -> re.Pattern[str]:
    """An event under `on:` in block, list-item, scalar or flow-sequence form, at any indent."""
    return re.compile(
        rf"^(?:on|'on'|\"on\"):\s*(?:(?:{events})\b|\[[^\]]*\b(?:{events})\b)"
        rf"|^\s+(?:-\s*)?(?:{events})\s*:?\s*(?:#.*)?$",
        re.M,
    )


_PRIVILEGED_TRIGGER = _trigger("pull_request_target|workflow_run")
_PR_HEAD_REF = re.compile(
    r"github\.event\.pull_request\.head\.(sha|ref)|github\.head_ref|refs/pull/"
)
# Any use of the secrets context except GITHUB_TOKEN: dot or index access, any case
# (secret names are case-insensitive), and whole-context uses such as toJSON(secrets).
# String literals are skipped whole, so a `}` or the word "secrets" inside quotes
# neither ends the expression early nor counts as a read.
_SECRET = re.compile(
    r"\$\{\{(?:'[^']*'|[^}'])*?\bsecrets\b(?!\s*(?:\.\s*GITHUB_TOKEN\b|\[\s*['\"]GITHUB_TOKEN['\"]\s*\]))",
    re.IGNORECASE,
)

# A workflow listed here may keep a privileged trigger; it must never check out
# PR code. Add to it only with a security reviewer's sign-off. post-release-probe
# runs on the release workflow (tag pushes only) and checks out the default branch.
PRIVILEGED_TRIGGER_ALLOWLIST = {"post-release-probe.yml"}

# A privileged workflow signed off by its content, not its name: the SHA-256 of
# the file's raw bytes, as `sha256sum .github/workflows/<name>` prints it. Any
# edit, or another file of the same name, fails until a security reviewer reads
# it and pins the new hash. A pinned file that is not in the tree has nothing to
# check. dco.yml is a sign-off check: it reads a pull request's commits through
# the API and fetches or checks out nothing of it.
PINNED_PRIVILEGED_WORKFLOWS = {
    "dco.yml": "4d2ad49edd32c642d57e6e997fa5cb6d1cf3ee7f59a46b3b609b7d6102169310",
}

_PULL_REQUEST_TRIGGER = _trigger("pull_request")
_SECRETS_INHERIT = re.compile(r"^\s+secrets:\s*inherit\s*$", re.M)
_FORK_GUARDS = {
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.repository == github.event.pull_request.head.repo.full_name",
}
# A conjunct that is false for every pull_request event.
_EXCLUDES_PULL_REQUEST = re.compile(
    r"^github\.event_name (?:!= 'pull_request'|== '(?!pull_request')[^']*')$"
)
_JOBS_KEY = re.compile(r"^jobs:\s*$", re.M)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _unsigned_privileged_workflows(
    paths: list[Path],
    allowlist: set[str] = PRIVILEGED_TRIGGER_ALLOWLIST,
    pinned: dict[str, str] = PINNED_PRIVILEGED_WORKFLOWS,
) -> list[str]:
    """Each privileged workflow that is neither allowlisted by name nor the exact file pinned."""
    offenders = []
    for p in paths:
        if not _PRIVILEGED_TRIGGER.search(_text(p)) or p.name in allowlist:
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if p.name not in pinned:
            offenders.append(f"{p.name}: privileged trigger with no security sign-off")
        elif actual != pinned[p.name]:
            offenders.append(
                f"{p.name}: changed since its security sign-off (pinned {pinned[p.name]}, now {actual}); "
                "a security reviewer must read it and pin the new hash"
            )
    return offenders


def _jobs(text: str) -> dict[str, tuple[str, int]]:
    """Job id -> (block, the indent of the job's own keys). Works for any indent width."""
    jobs_key = _JOBS_KEY.search(text)
    if jobs_key is None:
        return {}
    body = text[jobs_key.end():]
    next_top_level = re.search(r"^\S", body, re.M)
    if next_top_level is not None:
        body = body[: next_top_level.start()]
    first_key = re.search(r"^( +)[^\s#]", body, re.M)
    if first_key is None:
        return {}
    indent = len(first_key.group(1))
    header = re.compile(rf"^ {{{indent}}}([A-Za-z0-9_-]+):(?:[ \t]+#[^\r\n]*)?[ \t]*$", re.M)
    headers = list(header.finditer(body))
    jobs = {}
    for i, h in enumerate(headers):
        block = body[h.end(): headers[i + 1].start() if i + 1 < len(headers) else len(body)]
        first_job_key = re.search(r"^( +)[^\s#]", block, re.M)
        jobs[h.group(1)] = (block, len(first_job_key.group(1)) if first_job_key else indent + 2)
    return jobs


def _job_condition(block: str, key_indent: int) -> str | None:
    """The job-level `if:` plus any folded/continued lines indented deeper than it."""
    match = re.search(rf"^ {{{key_indent}}}if:(.*(?:\n {{{key_indent + 1},}}.*)*)", block, re.M)
    return match.group(1) if match else None


def _split_top_level(expr: str, op: str) -> list[str]:
    parts, depth, quoted, start, i = [], 0, False, 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        elif not quoted and depth == 0 and expr.startswith(op, i):
            parts.append(expr[start:i].strip())
            start = i + len(op)
            i = start
            continue
        i += 1
    parts.append(expr[start:].strip())
    return parts


def _strip_outer_parens(expr: str) -> str:
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        for i, ch in enumerate(expr):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i < len(expr) - 1:
                return expr
        expr = expr[1:-1].strip()
    return expr


def _normalize_condition(raw: str) -> str:
    lines = [re.sub(r"\s+#[^']*$", "", line) for line in raw.splitlines()]
    expr = " ".join(lines).strip()
    expr = re.sub(r"^[>|][-+]?", "", expr).strip()
    if len(expr) > 1 and expr[0] == expr[-1] and expr[0] in "\"'":
        expr = expr[1:-1]
    expr = re.sub(r"^\$\{\{(.*)\}\}$", r"\1", expr.strip()).strip()
    expr = re.sub(r"\s+", " ", expr)
    # Expression property names and string comparisons are case-insensitive.
    return re.sub(r"\s*(==|!=)\s*", r" \1 ", expr).lower()


def _admits_fork_pull_requests(expr: str) -> bool:
    """Fail closed: every top-level `||` branch must carry the fork guard as a top-level
    `&&` term or rule out pull_request events; anything this can't read admits forks."""
    for raw_branch in _split_top_level(_strip_outer_parens(expr), "||"):
        branch = _strip_outer_parens(raw_branch)
        if len(_split_top_level(branch, "||")) > 1:
            if _admits_fork_pull_requests(branch):
                return True
            continue
        terms = [_strip_outer_parens(t) for t in _split_top_level(branch, "&&")]
        if not any(t in _FORK_GUARDS or _EXCLUDES_PULL_REQUEST.match(t) for t in terms):
            return True
    return False


def _unguarded_secret_jobs(text: str) -> list[str]:
    """Jobs of a pull_request workflow that can read secrets without their own fork guard."""
    if not _PULL_REQUEST_TRIGGER.search(text):
        return []
    jobs_key = _JOBS_KEY.search(text)
    # Secrets in workflow-level env reach every job.
    workflow_level_secret = bool(jobs_key and _SECRET.search(text[: jobs_key.start()]))
    offenders = []
    for name, (block, key_indent) in _jobs(text).items():
        reads_secrets = workflow_level_secret or _SECRET.search(block) or _SECRETS_INHERIT.search(block)
        if not reads_secrets:
            continue
        condition = _job_condition(block, key_indent)
        if condition is None or _admits_fork_pull_requests(_normalize_condition(condition)):
            offenders.append(name)
    return offenders


class TestWorkflowTrust(unittest.TestCase):
    def test_workflows_found(self) -> None:
        self.assertTrue(WORKFLOWS, f"no workflows under {REPO}/.github/workflows; set REPO_ROOT")

    def test_no_job_bypasses_checkouts_pr_safety_guard(self) -> None:
        offenders = [p.name for p in WORKFLOWS if re.search(r"allow-unsafe-pr-checkout:\s*true", _text(p))]
        self.assertEqual(offenders, [], "allow-unsafe-pr-checkout: true runs PR code with secrets")

    def test_privileged_triggers_are_allowlisted(self) -> None:
        offenders = _unsigned_privileged_workflows(WORKFLOWS)
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

    def test_job_header_with_trailing_comment_is_checked(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  unguarded: # integration job\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
        )
        self.assertEqual(_unguarded_secret_jobs(workflow), ["unguarded"])

    def test_secret_references_in_any_case_and_syntax(self) -> None:
        for reference in (
            "${{ secrets.api_key }}",
            "${{ secrets['API_KEY'] }}",
            '${{ secrets["api_key"] }}',
            "${{ toJSON(secrets) }}",
            "${{ secrets.GITHUB_TOKEN || secrets.API_KEY }}",
            "${{ format('{0}', secrets.API_KEY) }}",
        ):
            with self.subTest(reference=reference):
                workflow = f"on:\n  pull_request:\njobs:\n  build:\n    steps:\n      - run: echo {reference}\n"
                self.assertEqual(_unguarded_secret_jobs(workflow), ["build"])
        for token in (
            "${{ secrets.github_token }}",
            "${{ secrets['GITHUB_TOKEN'] }}",
            "${{ contains(github.event.head_commit.message, 'secrets') }}",
            "no secrets here",
        ):
            with self.subTest(reference=token):
                workflow = f"on:\n  pull_request:\njobs:\n  build:\n    steps:\n      - run: echo {token}\n"
                self.assertEqual(_unguarded_secret_jobs(workflow), [])

    def test_conditions_are_judged_by_what_they_admit(self) -> None:
        guard = "github.event.pull_request.head.repo.full_name == github.repository"
        admits_forks = [
            f"github.event_name == 'pull_request' || {guard}",
            f"always() || {guard}",
            f"!({guard})",
            f"({guard} || github.actor == 'bot') && success()",
            f"github.event_name == 'Pull_Request' || {guard}",
            "github.event_name != 'push'",
            "always()",
        ]
        keeps_forks_out = [
            guard,
            f"${{{{ {guard} }}}}",
            "github.repository == github.event.pull_request.head.repo.full_name",
            f"github.event_name != 'pull_request' || ({guard} && !contains(github.event.pull_request.labels.*.name, 'x'))",
            "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'",
        ]
        for condition in admits_forks + keeps_forks_out:
            with self.subTest(condition=condition):
                workflow = (
                    "on:\n  pull_request:\njobs:\n  build:\n"
                    f"    if: >-\n      {condition}\n"
                    "    steps:\n      - run: echo ${{ secrets.API_KEY }}\n"
                )
                expected = ["build"] if condition in admits_forks else []
                self.assertEqual(_unguarded_secret_jobs(workflow), expected)

    def test_jobs_indented_by_four_spaces_are_checked(self) -> None:
        guard = "github.event.pull_request.head.repo.full_name == github.repository"
        workflow = (
            "on:\n"
            "    pull_request:\n"
            "jobs:\n"
            "    guarded:\n"
            f"        if: {guard}\n"
            "        steps:\n"
            "            - run: echo ${{ secrets.API_KEY }}\n"
            "    unguarded:\n"
            "        steps:\n"
            "            - run: echo ${{ secrets.API_KEY }}\n"
        )
        self.assertEqual(_unguarded_secret_jobs(workflow), ["unguarded"])

    def test_every_way_of_writing_the_trigger_is_recognised(self) -> None:
        job = "jobs:\n  build:\n    steps:\n      - run: echo ${{ secrets.API_KEY }}\n"
        for on in (
            "on: pull_request\n",
            "on: [push, pull_request]\n",
            "on:\n  - push\n  - pull_request\n",
            "on:\n  pull_request:\n    branches: [main]\n",
            "'on':\n  pull_request: # comment\n",
        ):
            with self.subTest(on=on):
                self.assertEqual(_unguarded_secret_jobs(on + job), ["build"])
        for on in ("on: pull_request_target\n", "on: [push]\n", "on:\n  push:\n"):
            with self.subTest(on=on):
                self.assertEqual(_unguarded_secret_jobs(on + job), [])
        for on in ("on: pull_request_target\n", "on: [push, workflow_run]\n", "on:\n  workflow_run:\n"):
            with self.subTest(privileged=on):
                self.assertTrue(_PRIVILEGED_TRIGGER.search(on))

    def test_jobs_without_secrets_and_non_pull_request_workflows_pass(self) -> None:
        no_secrets = "on:\n  pull_request:\njobs:\n  lint:\n    steps:\n      - run: echo ${{ secrets.GITHUB_TOKEN }}\n"
        push_only = "on:\n  push:\njobs:\n  deploy:\n    steps:\n      - run: echo ${{ secrets.API_KEY }}\n"
        self.assertEqual(_unguarded_secret_jobs(no_secrets), [])
        self.assertEqual(_unguarded_secret_jobs(push_only), [])



class TestPinnedPrivilegedWorkflows(unittest.TestCase):
    PRIVILEGED = "on:\n  pull_request_target:\njobs:\n  check:\n    steps:\n      - run: echo ok\n"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def workflow(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def pin(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_a_pinned_file_that_is_absent_passes(self) -> None:
        other = self.workflow("ci.yml", "on:\n  push:\njobs: {}\n")
        self.assertEqual(_unsigned_privileged_workflows([other], set(), {"dco.yml": self.pin(self.PRIVILEGED)}), [])

    def test_the_pinned_file_passes(self) -> None:
        dco = self.workflow("dco.yml", self.PRIVILEGED)
        self.assertEqual(_unsigned_privileged_workflows([dco], set(), {"dco.yml": self.pin(self.PRIVILEGED)}), [])

    def test_a_changed_file_fails_and_names_both_hashes(self) -> None:
        changed = self.PRIVILEGED + "      - run: gh pr checkout 1\n"
        dco = self.workflow("dco.yml", changed)
        offenders = _unsigned_privileged_workflows([dco], set(), {"dco.yml": self.pin(self.PRIVILEGED)})
        self.assertEqual(len(offenders), 1)
        self.assertIn(self.pin(self.PRIVILEGED), offenders[0])
        self.assertIn(self.pin(changed), offenders[0])
        self.assertIn("security reviewer", offenders[0])

    def test_a_privileged_workflow_of_another_name_fails(self) -> None:
        other = self.workflow("signoff.yml", self.PRIVILEGED)
        offenders = _unsigned_privileged_workflows([other], set(), {"dco.yml": self.pin(self.PRIVILEGED)})
        self.assertEqual(offenders, ["signoff.yml: privileged trigger with no security sign-off"])

    def test_the_name_allowlist_still_admits_its_own_workflows(self) -> None:
        probe = self.workflow("post-release-probe.yml", "on:\n  workflow_run:\njobs: {}\n")
        self.assertEqual(_unsigned_privileged_workflows([probe], {"post-release-probe.yml"}, {}), [])

    def test_the_repository_pins_are_full_sha256_digests(self) -> None:
        for name, digest in PINNED_PRIVILEGED_WORKFLOWS.items():
            with self.subTest(name=name):
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
                self.assertNotIn(name, PRIVILEGED_TRIGGER_ALLOWLIST, "a pinned file must not also be allowlisted by name")


if __name__ == "__main__":
    unittest.main()
