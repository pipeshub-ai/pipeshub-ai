#!/usr/bin/env python3
"""What a CI job may do: least privilege for every GitHub Actions workflow (SEC-D-12, ADR-SEC-05).

    python3 scripts/workflow_lint.py            # every rule over .github/; exit 1 on a finding

scripts/supply_chain.py decides what code enters a job (ACTION-PIN, PIPE-SHELL, DOWNLOAD-HASH); this decides what
the job can then do with its token, its secrets and the text it reads:

- WF-PERMS    the workflow sets `permissions:` and grants no write at the top; writes are per job.
- WF-PERSIST  every actions/checkout sets `persist-credentials: false`, so no later step finds the token in
              .git/config. A job that pushes is listed in contracts/supply-chain/workflow-exceptions.json.
- WF-EXPR     no attacker-writable expression (dispatch and call inputs, PR/issue/release/commit text, branch
              names) is spliced into a `run:` script or an actions/github-script `script:`; pass it through `env:`.
- WF-AGENT    an LLM agent step (it reads text other people wrote) has no shell, interpreter, web or MCP tool,
              no token in its environment, a job with no write permission, and checkouts without credentials.
- WF-TRIGGER  no pull_request_target or comment trigger (they run with secrets on untrusted input); a
              workflow_run follows only workflows that pull requests cannot start.
- WF-OWNERS   .github/CODEOWNERS names an owner for the paths that change what CI, releases and installs trust.

Needs PyYAML (backend/python/requirements/install.sh tools). The security gate runs it through
backend/python/tests/unit/harness/security/test_sec_d_12_workflows.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
EXCEPTIONS = Path("contracts/supply-chain/workflow-exceptions.json")

#: Actions that put a language model to work on text the job reads.
AGENT_ACTIONS = ("anthropics/claude-code-action", "anthropics/claude-code-base-action", "openai/codex-action",
                 "google-github-actions/run-gemini-cli")
#: Tools an agent reading untrusted text must not hold: each one is a way to run code or send data out.
AGENT_FORBIDDEN_TOOL = re.compile(r"\b(?:Bash|BashOutput|KillShell|WebFetch|WebSearch|NotebookEdit|Task|mcp__\w*)\b|\*")
TOKEN_ENV = re.compile(r"^(?:GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN|.*_TOKEN|.*_PAT)$")
#: Triggers that run a workflow with secrets on input an outsider controls.
UNSAFE_TRIGGERS = ("pull_request_target", "issue_comment", "pull_request_review_comment", "discussion_comment")
#: Paths whose change alters what CI, a release or an install trusts.
OWNED_PATHS = ("/.github/", "/.github/workflows/", "/contracts/supply-chain/", "/backend/python/requirements/",
               "/backend/python/harness_engines/vendor_rules.py", "/backend/python/harness_engines/trust_roots.py",
               "/deployment/", "/scripts/supply_chain.py", "/scripts/workflow_lint.py")

_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
# Text an outsider (or anyone who can dispatch a workflow) chooses. Numbers, SHAs and ids are not text.
_UNTRUSTED = re.compile(
    r"\b(?:inputs|github\.event\.inputs)\.[A-Za-z0-9_-]+"
    r"|\bgithub\.head_ref\b"
    r"|\bgithub\.event\.(?:pull_request|issue|comment|review|review_comment|discussion|release|head_commit|commits"
    r"|pages|workflow_run|merge_group|client_payload|ref|base_ref|forkee)\b[\w.\[\]*'\"]*"
)
_SAFE_SUFFIX = re.compile(r"\.(?:number|sha|id|run_id|run_number|run_attempt|draft|merged|prerelease|created_at"
                          r"|updated_at|html_url|url|state|full_name|fork|login)$")


@dataclass(frozen=True)
class Finding:
    rule: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.where}: {self.message}"


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _triggers(data: dict[str, Any]) -> dict[str, Any]:
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    on = data.get("on", data.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return dict.fromkeys(on)
    return on or {}


def _steps(job: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    yield from enumerate(job.get("steps") or [])


def _is_write(perms: Any) -> bool:
    if perms in ("write-all",):
        return True
    return isinstance(perms, dict) and any(str(v) == "write" for v in perms.values())


def load_exceptions(repo: Path) -> dict[tuple[str, str, str], str]:
    path = repo / EXCEPTIONS
    if not path.is_file():
        return {}
    items = json.loads(path.read_text(encoding="utf-8"))["exceptions"]
    return {(i["rule"], i["workflow"], i["job"]): i["reason"] for i in items}


def untrusted_expressions(text: str) -> list[str]:
    """The attacker-writable references spliced into `text` by `${{ }}`."""
    hits = []
    for expr in _EXPR.findall(text):
        for m in _UNTRUSTED.finditer(expr):
            ref = m.group(0).rstrip(".")
            if not _SAFE_SUFFIX.search(ref):
                hits.append(ref)
    return hits


def _check_job(name: str, job_id: str, job: dict[str, Any], exceptions: dict[tuple[str, str, str], str],
               used: set[tuple[str, str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    where = f"{name}:{job_id}"
    job_write = _is_write(job.get("permissions"))
    for i, step in _steps(job):
        label = f"{where}:step {i + 1}" + (f" ({step['name']})" if step.get("name") else "")
        uses = str(step.get("uses", ""))
        action = uses.split("@", 1)[0]
        if action == "actions/checkout":
            persist = (step.get("with") or {}).get("persist-credentials")
            key = ("WF-PERSIST", name, job_id)
            if persist is not False and str(persist).lower() != "false":
                if key in exceptions:
                    used.add(key)
                else:
                    findings.append(Finding("WF-PERSIST", label, "set `persist-credentials: false`; a job that must "
                                            f"push lists itself in {EXCEPTIONS} with its reason"))
        scripts = []
        if "run" in step:
            scripts.append(str(step["run"]))
        if action == "actions/github-script":
            scripts.append(str((step.get("with") or {}).get("script", "")))
        for script in scripts:
            for ref in untrusted_expressions(script):
                findings.append(Finding("WF-EXPR", label, f"`${{{{ {ref} }}}}` is spliced into a script; pass it "
                                        "through `env:` and read the variable"))
        if action in AGENT_ACTIONS:
            with_ = step.get("with") or {}
            tool_text = " ".join(str(with_.get(k, "")) for k in ("claude_args", "allowed_tools", "allowedTools"))
            allowed = re.findall(r"--allowed-?[Tt]ools[= ]+[\"']([^\"']*)[\"']", tool_text)
            allowed += [str(with_.get(k)) for k in ("allowed_tools", "allowedTools") if with_.get(k)]
            if not allowed:
                findings.append(Finding("WF-AGENT", label, "name the agent's tools (--allowedTools); its defaults "
                                        "may include a shell"))
            for spec in allowed:
                for tool in sorted({m.group(0) for m in AGENT_FORBIDDEN_TOOL.finditer(spec)}):
                    findings.append(Finding("WF-AGENT", label, f"the agent reads other people's text and may not hold "
                                            f"`{tool}`: it can run code or send data out"))
            env = {**(job.get("env") or {}), **(step.get("env") or {})}
            for var in sorted(k for k in env if TOKEN_ENV.match(str(k))):
                findings.append(Finding("WF-AGENT", label, f"{var} is in the agent's environment; it has no tool that "
                                        "needs it, and its file tools could read it"))
            if job_write:
                findings.append(Finding("WF-AGENT", where, "a job that runs an agent has write permissions; give it "
                                        "read only and push its output from a separate job"))
    return findings


def check_workflow(repo: Path, path: Path, exceptions: dict[tuple[str, str, str], str],
                   used: set[tuple[str, str, str]]) -> list[Finding]:
    name = path.name
    data = _load(path)
    findings: list[Finding] = []
    if "permissions" not in data:
        findings.append(Finding("WF-PERMS", name, "set top-level `permissions:` (contents: read, or {}); a job "
                                "without its own then gets read-only, not the repository default"))
    elif _is_write(data["permissions"]) or data["permissions"] == "read-all":
        findings.append(Finding("WF-PERMS", name, "top-level permissions grant more than read; move each write to "
                                "the job that needs it"))
    triggers = _triggers(data)
    for trigger in UNSAFE_TRIGGERS:
        if trigger in triggers:
            findings.append(Finding("WF-TRIGGER", name, f"`{trigger}` runs with secrets on input an outsider "
                                    "controls; use pull_request and a separate, gated job"))
    if "workflow_run" in triggers:
        followed = (triggers["workflow_run"] or {}).get("workflows") or []
        for other in sorted((repo / ".github" / "workflows").glob("*.y*ml")):
            o = _load(other)
            if o.get("name") in followed and {"pull_request", *UNSAFE_TRIGGERS} & set(_triggers(o)):
                findings.append(Finding("WF-TRIGGER", name, f"workflow_run follows {o.get('name')!r}, which a pull "
                                        "request starts: its artifacts are attacker-controlled"))
    for job_id, job in (data.get("jobs") or {}).items():
        findings += _check_job(name, str(job_id), job or {}, exceptions, used)
    return findings


def check_codeowners(repo: Path) -> list[Finding]:
    path = repo / ".github" / "CODEOWNERS"
    if not path.is_file():
        return [Finding("WF-OWNERS", ".github/CODEOWNERS", "missing: a change to CI, locks or signing needs no review")]
    # The last line naming a pattern wins, and a pattern with no owner after it unowns the path.
    owners: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        words = line.split("#", 1)[0].split()
        if words:
            owners[words[0]] = words[1:]
    return [Finding("WF-OWNERS", ".github/CODEOWNERS", f"{p} has no owner") for p in OWNED_PATHS if not owners.get(p)]


def run_checks(repo: Path = REPO) -> list[Finding]:
    exceptions = load_exceptions(repo)
    used: set[tuple[str, str, str]] = set()
    findings: list[Finding] = []
    for path in sorted((repo / ".github" / "workflows").glob("*.y*ml")):
        findings += check_workflow(repo, path, exceptions, used)
    findings += check_codeowners(repo)
    for key in sorted(set(exceptions) - used):
        findings.append(Finding(key[0], str(EXCEPTIONS), f"{key[1]}:{key[2]} is excused but needs no excuse; drop it"))
    return findings


def main() -> int:
    findings = run_checks(REPO)
    for f in findings:
        print(f, file=sys.stderr)
    print(f"workflow lint: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
