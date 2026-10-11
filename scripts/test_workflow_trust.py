"""Untrusted pull-request code must never share a job with repository secrets.

The secret checks parse each workflow with PyYAML (main.yml installs the
version integration-tests/uv.lock pins), since every regex reading of YAML
missed some spelling GitHub accepts. The rest is pattern-based.
"""

import os
import re
import unittest
from pathlib import Path
from typing import Any

import yaml

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
# Read after _dotted(): expression property names are case-insensitive. head.repo
# counts too, since checking out that repository on pull_request_target is the fork.
_PR_HEAD_REF = re.compile(
    r"github\.event\.pull_request\.head\.(?:sha|ref|repo)\b|github\.head_ref\b|refs/pull/",
    re.IGNORECASE,
)


def _dotted(text: str) -> str:
    """Expression property access in one spelling: `x['y']` and `x . y` become `x.y`.

    The expression lexer skips any Unicode whitespace between tokens, newlines included.
    """
    # Grouping parentheses don't change what is read: `(x.head).sha`, `x.head[('sha')]`.
    text = re.sub(r"[()]", "", text)
    text = re.sub(r"\[\s*(['\"])([A-Za-z0-9_-]+)\1\s*\]", r".\2", text)
    return re.sub(r"\s*\.\s*", ".", text)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _references_pr_head(text: str) -> bool:
    """The raw file or any parsed YAML string (quotes and folds resolved) reads the PR head."""
    if _PR_HEAD_REF.search(_dotted(text)):
        return True
    try:
        workflow = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError:
        return True
    return any(_PR_HEAD_REF.search(_dotted(s)) for s in _strings(workflow))


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

_FORK_GUARDS = {
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.repository == github.event.pull_request.head.repo.full_name",
}
# A conjunct that is false for every pull_request event.
_EXCLUDES_PULL_REQUEST = re.compile(
    r"^github\.event_name (?:!= 'pull_request'|== '(?!pull_request')[^']*')$"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate keys, where parsers may disagree on which wins."""


def _mapping_without_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=True)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates)


def _triggers(workflow: dict[Any, Any]) -> set[str]:
    # YAML 1.1 reads a bare `on` key as True.
    names: set[str] = set()
    for on in (workflow.get("on"), workflow.get(True)):
        if isinstance(on, str):
            names.add(on)
        elif isinstance(on, (list, dict)):
            names.update(str(event) for event in on)
    return names


def _privileged(text: str) -> bool:
    """A privileged trigger by the regex or by the parsed `on`; unreadable files count."""
    if _PRIVILEGED_TRIGGER.search(text):
        return True
    try:
        workflow = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError:
        return True
    return isinstance(workflow, dict) and bool(_triggers(workflow) & {"pull_request_target", "workflow_run"})


def _reads_secrets(value: Any) -> bool:
    """Any key or string anywhere below `value` reads a secret other than GITHUB_TOKEN."""
    if isinstance(value, str):
        return bool(_SECRET.search(value))
    if isinstance(value, dict):
        return any(_reads_secrets(k) or _reads_secrets(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_reads_secrets(v) for v in value)
    return False


def _secret_jobs(text: str) -> list[tuple[str, Any]]:
    """(job id, job) for each job of a pull_request workflow that can reach secrets.

    Fails closed: a workflow that doesn't parse, or whose jobs aren't a mapping,
    comes back as one pseudo-job so the caller reports it.
    """
    try:
        workflow = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        return [(f"<does not parse: {type(e).__name__}>", None)]
    if not isinstance(workflow, dict):
        return [("<not a mapping>", None)]
    if "pull_request" not in _triggers(workflow):
        return []
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return [("<jobs is not a mapping>", None)] if _reads_secrets(workflow) else []
    # Workflow-level env (anywhere outside jobs) reaches every job.
    shared = _reads_secrets({k: v for k, v in workflow.items() if k != "jobs"})
    found = []
    for job_id, job in jobs.items():
        # `secrets:` on a reusable-workflow call is `inherit` or a map of references;
        # any other value is something this check can't read, so it counts.
        passes = isinstance(job, dict) and job.get("secrets") is not None and not isinstance(job.get("secrets"), dict)
        if shared or passes or _reads_secrets(job):
            found.append((str(job_id), job))
    return found


def _unguarded_secret_jobs(text: str) -> list[str]:
    """Jobs of a pull_request workflow that can read secrets without their own fork guard."""
    offenders = []
    for job_id, job in _secret_jobs(text):
        condition = job.get("if") if isinstance(job, dict) else None
        if condition is None or _admits_fork_pull_requests(_normalize_condition(str(condition))):
            offenders.append(job_id)
    return offenders


# Environment values that resolve, on a pull_request event, to an environment
# with required reviewers in the repository settings (integration-test-dev).
# integration-test-daily has no reviewers, so it may only be picked for schedule.
# Add to this only after checking the environment's protection rules.
APPROVAL_GATED_ENVIRONMENTS = {
    "integration-test-dev",
    "${{ github.event_name == 'schedule' && 'integration-test-daily' || 'integration-test-dev' }}",
}


def _job_environment(job: Any) -> Any:
    environment = job.get("environment") if isinstance(job, dict) else None
    return environment.get("name") if isinstance(environment, dict) else environment


def _ungated_secret_jobs(text: str) -> list[str]:
    """Jobs of a pull_request workflow that read secrets without waiting for an approval."""
    return [
        job_id
        for job_id, job in _secret_jobs(text)
        if not isinstance(_job_environment(job), str) or _job_environment(job) not in APPROVAL_GATED_ENVIRONMENTS
    ]


class TestWorkflowTrust(unittest.TestCase):
    def test_workflows_found(self) -> None:
        self.assertTrue(WORKFLOWS, f"no workflows under {REPO}/.github/workflows; set REPO_ROOT")

    def test_no_job_bypasses_checkouts_pr_safety_guard(self) -> None:
        offenders = [p.name for p in WORKFLOWS if re.search(r"allow-unsafe-pr-checkout:\s*true", _text(p))]
        self.assertEqual(offenders, [], "allow-unsafe-pr-checkout: true runs PR code with secrets")

    def test_privileged_triggers_are_allowlisted(self) -> None:
        offenders = [
            p.name
            for p in WORKFLOWS
            if _privileged(_text(p)) and p.name not in PRIVILEGED_TRIGGER_ALLOWLIST
        ]
        self.assertEqual(offenders, [], "pull_request_target/workflow_run need a security sign-off")

    def test_privileged_workflows_never_check_out_pr_code(self) -> None:
        offenders = [
            p.name
            for p in WORKFLOWS
            if _privileged(_text(p)) and _references_pr_head(_text(p))
        ]
        self.assertEqual(offenders, [], "a privileged trigger checks out or references the PR head")

    def test_pull_request_jobs_with_secrets_skip_forks(self) -> None:
        # pull_request gives a fork no secrets anyway; this guards the self-hosted
        # runner and keeps the intent explicit: secrets only for same-repo heads.
        offenders = [f"{p.name}:{job}" for p in WORKFLOWS for job in _unguarded_secret_jobs(_text(p))]
        self.assertEqual(offenders, [], "pull_request job reads secrets without its own fork guard")

    def test_pull_request_jobs_with_secrets_wait_for_an_environment(self) -> None:
        # A same-repo head passes the fork guard, and its own code runs with
        # whatever the job logged in with, so a secret needs an approval first.
        offenders = [f"{p.name}:{job}" for p in WORKFLOWS for job in _ungated_secret_jobs(_text(p))]
        self.assertEqual(offenders, [], "pull_request job reads secrets with no environment approval")


class TestUngatedSecretJobs(unittest.TestCase):
    def test_a_secret_job_without_an_environment_is_flagged(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  gated:\n"
            "    environment: integration-test-dev\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  ungated:\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  token_only:\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.GITHUB_TOKEN }}\n"
        )
        self.assertEqual(_ungated_secret_jobs(workflow), ["ungated"])

    def test_a_step_environment_variable_is_not_an_environment(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  build:\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "        environment: nope\n"
        )
        self.assertEqual(_ungated_secret_jobs(workflow), ["build"])

    def test_an_environment_without_reviewers_is_not_a_gate(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  daily:\n"
            "    environment: integration-test-daily\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  named:\n"
            "    environment:\n"
            "      name: ${{ github.event_name == 'schedule' && 'integration-test-daily' || 'integration-test-dev' }}\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  made_up:\n"
            "    environment: unprotected\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  stray_quote:\n"
            "    environment: integration-test-dev'\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  hash_in_quotes:\n"
            "    environment:\n"
            "      name: \"integration-test-dev # x\"\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  trailing_nbsp:\n"
            "    environment: integration-test-dev\u00a0\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  leading_nbsp:\n"
            "    environment: \u00a0integration-test-dev\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  folded:\n"
            "    environment: integration-test-dev\n"
            "      extra\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  folded_name:\n"
            "    environment:\n"
            "      name: ${{ github.event_name == 'schedule' && 'integration-test-daily' || 'integration-test-dev' }}\n"
            "        x\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  plain_with_comment:\n"
            "    environment: integration-test-dev # the gated one\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
        )
        self.assertEqual(
            _ungated_secret_jobs(workflow),
            [
                "daily", "made_up", "stray_quote", "hash_in_quotes",
                "trailing_nbsp", "leading_nbsp", "folded", "folded_name",
            ],
        )

    def test_jobs_the_header_pattern_misses_are_still_checked(self) -> None:
        workflow = (
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  \"build\":\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  ? lint\n"
            "  :\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
            "  leak:\n"
            "    uses: ./.github/workflows/called.yml\n"
            "    secrets: inherit # pass them\n"
            "  gated:\n"
            "    environment: integration-test-dev\n"
            "    steps:\n"
            "      - run: echo ${{ secrets.API_KEY }}\n"
        )
        expected = ["build", "lint", "leak"]
        self.assertEqual(_ungated_secret_jobs(workflow), expected)
        self.assertEqual(_ungated_secret_jobs(workflow.replace("\n", "\r\n")), expected)


    def test_every_yaml_spelling_of_a_secret_job_is_flagged(self) -> None:
        job = "  build:\n    steps:\n      - run: echo ${{ secrets.API_KEY }}\n"
        cases = {
            "commented jobs key": "on:\n  pull_request:\njobs: # keep\n" + job,
            "quoted jobs key": 'on:\n  pull_request:\n"jobs":\n' + job,
            "explicit jobs key": "on:\n  pull_request:\n? jobs\n:\n" + job,
            "space before colon": "on:\n  pull_request:\njobs :\n" + job,
            "quoted trigger": 'on: "pull_request"\njobs:\n' + job,
            "quoted trigger key": 'on:\n  "pull_request":\njobs:\n' + job,
            "flow trigger": "on: {pull_request: {branches: [main]}}\njobs:\n" + job,
            "flow jobs": 'on: pull_request\njobs: {build: {steps: [{run: "echo ${{ secrets.API_KEY }}"}]}}\n',
            "flow job": 'on: pull_request\njobs:\n  build: {steps: [{run: "echo ${{ secrets.API_KEY }}"}]}\n',
            "root env after jobs": "on: pull_request\njobs:\n  build:\n    steps:\n      - run: echo hi\n"
            "env:\n  TOKEN: ${{ secrets.API_KEY }}\n",
        }
        for label, workflow in cases.items():
            with self.subTest(label):
                self.assertEqual(_ungated_secret_jobs(workflow), ["build"])
                self.assertEqual(_ungated_secret_jobs(workflow.replace("\n", "\r\n")), ["build"])

    def test_every_spelling_of_secrets_inherit_counts(self) -> None:
        for secrets in (
            "secrets: inherit",
            'secrets: "inherit"',
            "secrets: 'inherit'",
            "secrets : inherit",
            '"secrets": inherit',
            "secrets: >-\n      inherit",
            "secrets:\n      inherit",
        ):
            with self.subTest(secrets=secrets):
                workflow = f"on: pull_request\njobs:\n  leak:\n    uses: ./.github/workflows/x.yml\n    {secrets}\n"
                self.assertEqual(_ungated_secret_jobs(workflow), ["leak"])
                self.assertEqual(_unguarded_secret_jobs(workflow), ["leak"])

    def test_a_workflow_that_cannot_be_read_is_reported(self) -> None:
        duplicate = "on: pull_request\njobs:\n  build:\n    steps: []\n  build:\n    steps: []\n"
        self.assertEqual(len(_ungated_secret_jobs(duplicate)), 1)
        self.assertEqual(len(_ungated_secret_jobs("on: [pull_request\n")), 1)

    def test_the_gated_forms_the_workflows_use_stay_gated(self) -> None:
        for environment in (
            "environment: integration-test-dev",
            "environment:\n      name: ${{ github.event_name == 'schedule' && 'integration-test-daily' || 'integration-test-dev' }}",
        ):
            with self.subTest(environment=environment):
                workflow = (
                    "on: pull_request\njobs:\n  build:\n"
                    f"    {environment}\n"
                    "    steps:\n      - run: echo ${{ secrets.API_KEY }}\n"
                )
                self.assertEqual(_ungated_secret_jobs(workflow), [])
                self.assertEqual(_ungated_secret_jobs(workflow.replace("\n", "\r\n")), [])

    def test_every_spelling_of_a_privileged_trigger_and_head_ref_is_seen(self) -> None:
        for on in (
            'on: "pull_request_target"\n',
            "on: {pull_request_target: {types: [opened]}}\n",
            'on:\n  "pull_request_target":\n',
            "on: [push, 'workflow_run']\n",
        ):
            with self.subTest(on=on):
                self.assertTrue(_privileged(on + "jobs:\n  a:\n    steps: []\n"))
        self.assertFalse(_privileged("on: pull_request\njobs:\n  a:\n    steps: []\n"))
        for ref in (
            "github.event.pull_request.head.sha",
            "github.event.pull_request.head['sha']",
            'github.event.pull_request.head["ref"]',
            "github.event.pull_request['head']['sha']",
            "github.event.pull_request['head'].sha",
            "github['event'].pull_request.head.sha",
            "github.event.pull_request.head . sha",
            "github.event.pull_request.head.repo.full_name",
            "GITHUB.EVENT.PULL_REQUEST.HEAD.SHA",
            "github['head_ref']",
        ):
            with self.subTest(ref=ref):
                self.assertTrue(_PR_HEAD_REF.search(_dotted(f"ref: ${{{{ {ref} }}}}")))
        self.assertFalse(_PR_HEAD_REF.search(_dotted("ref: ${{ github.event.pull_request.base.sha }}")))
        for step in (
            "ref: |\n            ${{ github.event.pull_request.head\n            .sha }}\n",
            "ref: ${{ github.event.pull_request.head\u00a0.sha }}\n",
            "ref: '${{ github.event.pull_request.head[''sha''] }}'\n",
            "ref: ${{ (github.event.pull_request.head).sha }}\n",
            "ref: ${{ github.event.pull_request.head[('sha')] }}\n",
            "ref: ${{ ((github.event.pull_request.head)).sha }}\n",
        ):
            with self.subTest(step=step):
                self.assertTrue(_references_pr_head(f"jobs:\n  a:\n    steps:\n      - with:\n          {step}"))
        self.assertFalse(_references_pr_head("jobs:\n  a:\n    steps:\n      - with:\n          ref: main\n"))
        self.assertFalse(_references_pr_head("jobs:\n  a:\n    steps:\n      - with:\n          ref: ${{ (github.event.pull_request.base).sha }}\n"))

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


if __name__ == "__main__":
    unittest.main()
