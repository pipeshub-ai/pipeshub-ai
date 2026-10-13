#!/usr/bin/env python3
"""Turn the JUnit files of an SDK test run into one verdict and a report.

Usage: report.py <reports dir> --out <dir> [--releases <file>]
       report.py <reports dir> --current-passed

<reports dir> holds current-<language>.xml and, when the previous run ran,
previous-<language>.xml, as written by run_sdk_tests.sh.

Verdicts:
    OK     the generated SDK and the last released SDK both work with the app
    FLAG   the generated SDK works, the last released SDK does not, or its run
           produced no results
    BLOCK  the generated SDK does not work, or produced no results

--current-passed prints the languages whose current run passed, one per line;
the previous run starts only for those. --releases is .github/sdk-test-matrix.yaml;
the report names the tag each previous run used.

--out writes summary.md (the job summary and the pull request comment),
verdict.txt, and slack.json: the Slack message and the failures for its thread,
as chat.postMessage bodies without the channel.

A skipped test counts as a failure: Speakeasy writes a skipped placeholder for a
case it could not turn into code, so a skip means the case never ran. A suite
whose <run>-<language>.exit (from run_sdk_tests.sh) is not 0 fails even when its
JUnit file has no failed test: vitest, for one, leaves unhandled errors out of it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

LANGUAGES = ("typescript", "python", "go")
CURRENT = "current"
PREVIOUS = "previous"
# The pull request step finds its own earlier comment by this line.
COMMENT_MARKER = "<!-- sdk-tests-report -->"
# Second summary line: the previous-run failures an approval of a FLAG covers.
FLAG_CASES_PREFIX = "<!-- sdk-flag-cases:"


class Verdict(str, Enum):
    OK = "OK"
    FLAG = "FLAG"
    BLOCK = "BLOCK"


MEANING = {
    Verdict.OK: "The SDKs generated from this commit and the last released SDKs work with this app.",
    Verdict.FLAG: (
        "This change breaks the last released SDK. It can merge: a person adds the label "
        "`sdk-break-approved` after reading the failures, and adds a changelog note."
    ),
    Verdict.BLOCK: (
        "An SDK generated from this commit fails against this app, or the run could not "
        "test it. Fix it before merging."
    ),
}
_SLACK_ICONS = {
    Verdict.OK: ":large_green_circle:",
    Verdict.FLAG: ":warning:",
    Verdict.BLOCK: ":red_circle:",
}


class Status(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Case:
    name: str
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class LanguageResult:
    language: str
    current: dict[str, Case] | None
    previous: dict[str, Case] | None

    @property
    def current_failures(self) -> list[Case]:
        return _failures(self.current)

    @property
    def previous_failures(self) -> list[Case]:
        return _failures(self.previous)

    @property
    def verdict(self) -> Verdict:
        if not self.current or self.current_failures:
            return Verdict.BLOCK
        if self.previous_failures:
            return Verdict.FLAG
        return Verdict.OK


def _failures(cases: dict[str, Case] | None) -> list[Case]:
    return [case for case in (cases or {}).values() if case.status is not Status.PASSED]


def case_id(test_name: str) -> str:
    """One ID for the same case in every SDK.

    "Agents Agent Lifecycle" (vitest), "test_agents_agent_lifecycle" (pytest) and
    "TestAgents_AgentLifecycle" (go test) all become "agentsagentlifecycle".
    """
    return re.sub(r"[^a-z0-9]", "", test_name.lower()).removeprefix("test")


def _problem_detail(problem: ET.Element) -> str:
    """The error of a <failure> or <error>, on one line."""
    message = (problem.get("message") or "").strip()
    if message and message != "Failed":
        return message
    # go-junit-report writes only "Failed"; the error is in the body, spread over
    # testify's labelled lines. Keep the lines that say what went wrong.
    lines = (line.strip() for line in (problem.text or "").splitlines())
    kept = [
        line
        for line in lines
        if line and not line.startswith(("Error Trace:", "Test:"))
    ]
    return re.sub(r"\s+", " ", " ".join(kept)) or message


def read_junit(path: Path) -> dict[str, Case] | None:
    """Cases by ID, or None when the suite wrote no report."""
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        # A suite that crashed while writing its report; no results reads as BLOCK.
        return None
    cases: dict[str, Case] = {}
    for element in root.iter("testcase"):
        name = element.get("name", "")
        problem = element.find("failure")
        if problem is None:
            problem = element.find("error")
        if problem is not None:
            cases[case_id(name)] = Case(name, Status.FAILED, _problem_detail(problem))
        elif element.find("skipped") is not None:
            cases[case_id(name)] = Case(
                name, Status.SKIPPED, "skipped: the case never ran"
            )
        else:
            cases[case_id(name)] = Case(name, Status.PASSED)
    return cases


def read_releases(path: Path | None) -> dict[str, str]:
    """language -> tag from the flat "language: tag" lines of the release list."""
    if path is None:
        return {}
    releases = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("#")[0].partition(":")
        if key.strip() and value.strip():
            releases[key.strip()] = value.strip()
    return releases


def read_suite(reports_dir: Path, run: str, language: str) -> dict[str, Case] | None:
    """Cases of one suite, with a failed case added when it exited non-zero yet listed none."""
    cases = read_junit(reports_dir / f"{run}-{language}.xml")
    exit_file = reports_dir / f"{run}-{language}.exit"
    code = exit_file.read_text(encoding="utf-8").strip() if exit_file.is_file() else "0"
    if cases is not None and code != "0" and not _failures(cases):
        detail = (
            f"the suite exited with code {code} but reported no failed test; "
            f"see {run}-{language}.log"
        )
        cases = {**cases, f"{run}suite": Case(f"{run} suite", Status.FAILED, detail)}
    return cases


def read_results(reports_dir: Path) -> list[LanguageResult]:
    results = []
    for language in LANGUAGES:
        current = read_suite(reports_dir, CURRENT, language)
        previous = read_suite(reports_dir, PREVIOUS, language)
        if previous is None and current and not _failures(current):
            # The previous run starts for every language whose current run passed,
            # so no results means its checkout, install or suite broke.
            detail = "the previous run wrote no results; see the job log"
            previous = {"previoussuite": Case("previous suite", Status.FAILED, detail)}
        results.append(LanguageResult(language, current, previous))
    return results


def overall_verdict(results: list[LanguageResult]) -> Verdict:
    verdicts = {result.verdict for result in results}
    if Verdict.BLOCK in verdicts:
        return Verdict.BLOCK
    if Verdict.FLAG in verdicts:
        return Verdict.FLAG
    return Verdict.OK


def _cell(cases: dict[str, Case] | None, case: str) -> str:
    if cases is None:
        return "not run"
    if case not in cases:
        return "no such case"
    return "pass" if cases[case].status is Status.PASSED else "FAIL"


def _counts(cases: dict[str, Case] | None) -> str:
    if cases is None:
        return "not run"
    return f"{len(cases) - len(_failures(cases))}/{len(cases)} passed"


def _previous_label(language: str, releases: dict[str, str]) -> str:
    return releases.get(language, "last release")


def _failure_lines(
    results: list[LanguageResult], releases: dict[str, str]
) -> list[str]:
    """One line per failed case: language, run, case name, first line of the error."""
    no_current = [
        f"{result.language} {CURRENT}: no results; generation, the install or the "
        "suite failed, see the job log"
        for result in results
        if result.current is None
    ]
    return no_current + [
        f"{result.language} {run}: {case.name}: "
        f"{(case.detail.splitlines() or ['no detail'])[0][:300]}"
        for result in results
        for run, cases in (
            (CURRENT, result.current_failures),
            (
                f"{PREVIOUS} ({_previous_label(result.language, releases)})",
                result.previous_failures,
            ),
        )
        for case in cases
    ]


def render_summary(
    results: list[LanguageResult], releases: dict[str, str] | None = None
) -> str:
    releases = releases or {}
    verdict = overall_verdict(results)
    flag_cases = sorted(
        f"{r.language}/{key}"
        for r in results
        for key, case in (r.previous or {}).items()
        if case.status is not Status.PASSED
    )
    lines = [
        COMMENT_MARKER,
        f"{FLAG_CASES_PREFIX} {' '.join(flag_cases)} -->",
        f"## SDK tests: {verdict.value}",
        "",
        MEANING[verdict],
        "",
        "| SDK | Verdict | Current run | Previous run |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r.language} | {r.verdict.value} | {_counts(r.current)} | "
        f"{_counts(r.previous)} ({_previous_label(r.language, releases)}) |"
        for r in results
    ]

    names: dict[str, str] = {}
    for result in results:
        for cases in (result.current, result.previous):
            for key, case in (cases or {}).items():
                names.setdefault(key, case.name)

    header = " | ".join(
        f"{r.language} current | {r.language} previous" for r in results
    )
    lines += ["", f"| Case | {header} |", "| --- |" + " --- | --- |" * len(results)]
    for key in sorted(names):
        cells = " | ".join(
            f"{_cell(r.current, key)} | {_cell(r.previous, key)}" for r in results
        )
        lines.append(f"| {names[key]} | {cells} |")

    failures = _failure_lines(results, releases)
    if failures:
        lines += ["", "### Failures", "", *(f"- {line}" for line in failures)]
    return "\n".join(lines) + "\n"


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_payloads(
    results: list[LanguageResult],
    run_url: str,
    trigger: str,
    releases: dict[str, str] | None = None,
) -> dict[str, object]:
    """The verdict message and, when a case failed, a thread reply listing the failures.

    trigger is Slack mrkdwn from the workflow, such as a link to the pull request.
    """
    releases = releases or {}
    verdict = overall_verdict(results)
    title = f"{_SLACK_ICONS[verdict]} SDK tests: {verdict.value}"
    current = "\n".join(f"{r.language}: {_counts(r.current)}" for r in results)
    previous = "\n".join(
        f"{r.language} {_previous_label(r.language, releases)}: {_counts(r.previous)}"
        for r in results
    )
    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title, "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": MEANING[verdict]}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Trigger:*\n{trigger or 'unknown'}"},
                {"type": "mrkdwn", "text": f"*Current run (SDK from CI):*\n{current}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Previous run (last release):*\n{previous}",
                },
            ],
        },
    ]
    if run_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open the run"},
                        "url": run_url,
                    }
                ],
            }
        )
    failures = _failure_lines(results, releases)
    return {
        "message": {"text": title, "blocks": blocks},
        "thread": {"text": "\n".join(f"• {_slack_escape(line)}" for line in failures)}
        if failures
        else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports_dir", type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--out", type=Path)
    output.add_argument("--current-passed", action="store_true")
    parser.add_argument("--releases", type=Path)
    args = parser.parse_args()

    results = read_results(args.reports_dir)
    if args.current_passed:
        # One print per language: no language must mean no output, not an empty line.
        for r in results:
            if r.current and not r.current_failures:
                print(r.language)
        return 0

    verdict = overall_verdict(results)
    releases = read_releases(args.releases)
    summary = render_summary(results, releases)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.md").write_text(summary, encoding="utf-8")
    (args.out / "verdict.txt").write_text(verdict.value + "\n", encoding="utf-8")
    (args.out / "slack.json").write_text(
        json.dumps(
            slack_payloads(
                results,
                os.getenv("SDK_TEST_RUN_URL", ""),
                os.getenv("SDK_TEST_TRIGGER", ""),
                releases,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary)

    print(summary)
    return 1 if verdict is Verdict.BLOCK else 0


if __name__ == "__main__":
    raise SystemExit(main())
