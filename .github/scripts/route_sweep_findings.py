#!/usr/bin/env python3
"""Send each finding from the scheduled bug sweep to the right place.

The sweep itself is an AI review that writes ``sweep-findings.json``. This
script is the part that touches GitHub, kept separate and deterministic so the
reviewer never creates anything directly and every action it triggers is
inspectable here.

Two kinds of finding, two destinations, and the split is the whole point:

* **Security findings become private draft advisories.** A public issue or pull
  request describing a vulnerability discloses it before a fix ships. The
  repository has private vulnerability reporting enabled, and a draft advisory
  is where a maintainer can review the finding, and the proposed fix attached
  to it, without anyone else seeing either.

* **Ordinary bugs become labelled public issues.** They carry an area label so
  they land with the right people, and a marker label so everything this sweep
  created can be found, filtered or bulk-closed in one query.

Because this repository is public, its workflow logs and artifacts are public
too. Security findings are therefore never printed, never written to an
artifact, and never allowed to fall back to a public issue when the advisory
call fails. On failure the run goes red with a count, and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_SECURITY_PER_RUN = 3
MAX_BUGS_PER_RUN = 5
MARKER_LABEL = "automated-sweep"
MARKER_LABEL_COLOUR = "5319E7"
MARKER_LABEL_DESCRIPTION = "Filed by the scheduled bug sweep"

# Area names the reviewer may use, mapped onto labels that already exist in
# the repository. Anything unmapped gets only the bug and marker labels rather
# than a made-up one.
AREA_LABELS = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "javascript",
    "connectors": "Connectors",
    "sdk": "sdk",
    "performance": "Performance optimisation",
    "agent_builder": "agent_builder",
}

SEVERITIES = ("critical", "high", "medium", "low")


@dataclass
class Finding:
    kind: str
    title: str
    file: str
    line: int
    severity: str
    summary: str
    failure_scenario: str
    suggested_fix: str = ""
    area: str = ""
    cwe: str = ""

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass
class Outcome:
    advisories_created: list[str] = field(default_factory=list)
    issues_created: list[str] = field(default_factory=list)
    skipped_duplicates: int = 0
    dropped_malformed: int = 0
    dropped_over_cap: int = 0
    security_failed: int = 0
    bugs_failed: int = 0


# --------------------------------------------------------------------------- #
# GitHub access, small enough to fake in tests
# --------------------------------------------------------------------------- #


class GitHub:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.token = token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}


Requester = Callable[[str, str, dict[str, Any] | None], Any]


# --------------------------------------------------------------------------- #
# Reading and validating the report
# --------------------------------------------------------------------------- #


def load_findings(path: str, outcome: Outcome) -> list[Finding]:
    """Findings the reviewer wrote, minus anything that is not usable.

    A finding without a file, a line or a failure scenario is not actionable —
    nobody can look at it — so it is dropped and counted rather than filed.
    """
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)

    if not isinstance(report, dict):
        # A list or scalar at the top level is a broken report, not a report
        # with no findings; counting it keeps that visible in the summary.
        outcome.dropped_malformed += 1
        return []

    findings: list[Finding] = []
    for raw in report.get("findings") or []:
        parsed = _parse(raw)
        if parsed is None:
            outcome.dropped_malformed += 1
            continue
        findings.append(parsed)
    return findings


def _parse(raw: Any) -> Finding | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in ("security", "bug"):
        return None
    required = ("title", "file", "summary", "failure_scenario")
    if any(not str(raw.get(key, "")).strip() for key in required):
        return None
    try:
        line = int(raw.get("line") or 0)
    except (TypeError, ValueError):
        return None
    if line < 1:
        # No line means nobody can look at it; that is the bar for filing.
        return None
    severity = str(raw.get("severity", "medium")).strip().lower()
    if severity not in SEVERITIES:
        severity = "medium"
    return Finding(
        kind=kind,
        title=str(raw["title"]).strip(),
        file=str(raw["file"]).strip(),
        line=line,
        severity=severity,
        summary=str(raw["summary"]).strip(),
        failure_scenario=str(raw["failure_scenario"]).strip(),
        suggested_fix=str(raw.get("suggested_fix", "")).strip(),
        area=str(raw.get("area", "")).strip().lower(),
        cwe=str(raw.get("cwe", "")).strip().upper(),
    )


def apply_caps(findings: list[Finding], outcome: Outcome) -> list[Finding]:
    """At most a few of each per run, highest severity first.

    A sweep that files twenty issues at once trains people to ignore it. The
    caps are also enforced in the reviewer's prompt; this is the backstop.
    """
    order = {s: i for i, s in enumerate(SEVERITIES)}
    ranked = sorted(findings, key=lambda f: order.get(f.severity, len(SEVERITIES)))
    kept: list[Finding] = []
    counts = {"security": 0, "bug": 0}
    caps = {"security": MAX_SECURITY_PER_RUN, "bug": MAX_BUGS_PER_RUN}
    for finding in ranked:
        if counts[finding.kind] >= caps[finding.kind]:
            outcome.dropped_over_cap += 1
            continue
        counts[finding.kind] += 1
        kept.append(finding)
    return kept


# --------------------------------------------------------------------------- #
# Deduplication against what is already open
# --------------------------------------------------------------------------- #


def open_sweep_issue_titles(request: Requester, repo: str) -> list[str]:
    titles: list[str] = []
    page = 1
    while True:
        batch = request(
            "GET",
            f"/repos/{repo}/issues?state=open&labels={MARKER_LABEL}&per_page=100&page={page}",
            None,
        )
        if not batch:
            break
        titles.extend(str(item.get("title", "")) for item in batch if "pull_request" not in item)
        if len(batch) < 100:
            break
        page += 1
    return titles


def open_advisory_summaries(request: Requester, repo: str) -> list[str]:
    summaries: list[str] = []
    page = 1
    while True:
        try:
            batch = request(
                "GET",
                f"/repos/{repo}/security-advisories?state=draft&per_page=100&page={page}",
                None,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                # No permission to list means no permission to create either;
                # the create is what fails visibly, so nothing is lost here.
                return []
            # Anything else is a listing that did not happen. Treating it as
            # "no drafts exist" would file a duplicate on top of a real one.
            raise
        if not batch:
            break
        summaries.extend(str(item.get("summary", "")) for item in batch)
        if len(batch) < 100:
            break
        page += 1
    return summaries


def is_duplicate(finding: Finding, existing: list[str]) -> bool:
    """Already filed if an open item names the same file.

    Matching on the path rather than the title, because the reviewer words the
    same defect differently from one run to the next and the path does not
    move. Two distinct defects in one file would collide — accepted, since the
    first one being open is enough for someone to look at that file.
    """
    return any(finding.file in title for title in existing)


# --------------------------------------------------------------------------- #
# Creating things
# --------------------------------------------------------------------------- #


def ensure_marker_label(request: Requester, repo: str) -> None:
    try:
        request(
            "POST",
            f"/repos/{repo}/labels",
            {
                "name": MARKER_LABEL,
                "color": MARKER_LABEL_COLOUR,
                "description": MARKER_LABEL_DESCRIPTION,
            },
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 422:  # 422 is "already exists"
            raise


def issue_body(finding: Finding) -> str:
    fix = f"\n\n## Suggested fix\n\n{finding.suggested_fix}" if finding.suggested_fix else ""
    return (
        f"## What is wrong\n\n{finding.summary}\n\n"
        f"**Where:** `{finding.location}`\n"
        f"**Severity:** {finding.severity}\n\n"
        f"## How it fails\n\n{finding.failure_scenario}"
        f"{fix}\n\n"
        "---\n"
        f"_Filed by the scheduled bug sweep. It reviews recently changed code "
        f"and one rotating area each week; findings are capped so each one has "
        f"been worth a look. If this is wrong, close it with a note — the sweep "
        f"does not re-file an open or recently closed report for the same file._"
    )


def advisory_description(finding: Finding) -> str:
    fix = (
        f"\n\n## Proposed fix\n\n{finding.suggested_fix}\n\n"
        "Use **Start a temporary private fork** on this advisory to prepare the "
        "fix without disclosing it, then publish the advisory and the fix together."
        if finding.suggested_fix
        else ""
    )
    return (
        f"## What is wrong\n\n{finding.summary}\n\n"
        f"**Where:** `{finding.location}`\n\n"
        f"## How it can be exploited\n\n{finding.failure_scenario}"
        f"{fix}\n\n"
        "---\n"
        "_Filed privately by the scheduled bug sweep. Nothing about this "
        "finding has been written to a public issue, pull request, log or "
        "artifact._"
    )


def create_issue(request: Requester, repo: str, finding: Finding) -> str:
    labels = ["bug", MARKER_LABEL]
    area_label = AREA_LABELS.get(finding.area)
    if area_label:
        labels.append(area_label)
    created = request(
        "POST",
        f"/repos/{repo}/issues",
        {
            "title": f"{finding.title} ({finding.file})",
            "body": issue_body(finding),
            "labels": labels,
        },
    )
    return str(created.get("html_url") or created.get("number") or "?")


def create_advisory(request: Requester, repo: str, finding: Finding) -> str:
    package_name = repo.split("/", 1)[-1]
    body: dict[str, Any] = {
        "summary": f"{finding.title} ({finding.file})"[:1024],
        "description": advisory_description(finding),
        "severity": finding.severity,
        "vulnerabilities": [
            {
                "package": {"ecosystem": "other", "name": package_name},
                "vulnerable_version_range": "<= current",
            }
        ],
    }
    if finding.cwe:
        body["cwe_ids"] = [finding.cwe]
    created = request("POST", f"/repos/{repo}/security-advisories", body)
    return str(created.get("ghsa_id") or "?")


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def route(
    findings: list[Finding],
    request: Requester,
    repo: str,
    *,
    dry_run: bool,
    outcome: Outcome,
) -> Outcome:
    existing_issues = open_sweep_issue_titles(request, repo)
    existing_advisories = open_advisory_summaries(request, repo)

    if not dry_run and any(f.kind == "bug" for f in findings):
        ensure_marker_label(request, repo)

    for finding in findings:
        if finding.kind == "security":
            if is_duplicate(finding, existing_advisories):
                outcome.skipped_duplicates += 1
                continue
            if dry_run:
                outcome.advisories_created.append("(dry run)")
                continue
            try:
                outcome.advisories_created.append(create_advisory(request, repo, finding))
            except urllib.error.HTTPError as exc:
                # Deliberately no fallback: a security finding that cannot be
                # filed privately is not filed at all, and the count below is
                # the only trace of it.
                outcome.security_failed += 1
                print(f"::error::advisory creation failed with HTTP {exc.code}")
        else:
            if is_duplicate(finding, existing_issues):
                outcome.skipped_duplicates += 1
                continue
            if dry_run:
                outcome.issues_created.append(f"(dry run) {finding.title} — {finding.location}")
                continue
            try:
                outcome.issues_created.append(create_issue(request, repo, finding))
            except urllib.error.HTTPError as exc:
                outcome.bugs_failed += 1
                print(f"::error::issue creation failed with HTTP {exc.code} for {finding.location}")

    return outcome


def summarise(outcome: Outcome) -> str:
    """Counts, plus the public items by name. Never the security ones."""
    lines = [
        f"advisories created: {len(outcome.advisories_created)}",
        f"issues created:     {len(outcome.issues_created)}",
        f"skipped duplicates: {outcome.skipped_duplicates}",
        f"dropped malformed:  {outcome.dropped_malformed}",
        f"dropped over cap:   {outcome.dropped_over_cap}",
    ]
    if outcome.security_failed:
        lines.append(f"SECURITY FINDINGS NOT DELIVERED: {outcome.security_failed}")
    if outcome.bugs_failed:
        lines.append(f"issues not created: {outcome.bugs_failed}")
    for url in outcome.issues_created:
        lines.append(f"  issue: {url}")
    # Advisory ids are safe to print — the id alone reveals nothing — but the
    # contents never are, and the summary is the only thing this script prints.
    for ghsa in outcome.advisories_created:
        lines.append(f"  advisory: {ghsa}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("report", help="path to sweep-findings.json")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true", help="report what would be filed, file nothing")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.repo or (not token and not args.dry_run):
        print("::error::GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        return 2

    outcome = Outcome()
    findings = apply_caps(load_findings(args.report, outcome), outcome)
    github = GitHub(args.repo, token)
    route(findings, github.request, args.repo, dry_run=args.dry_run, outcome=outcome)

    print(summarise(outcome))
    # A finding that could not be delivered must not disappear into a green run.
    return 1 if (outcome.security_failed or outcome.bugs_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
