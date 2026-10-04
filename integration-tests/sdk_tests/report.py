#!/usr/bin/env python3
"""Turn the JUnit files of an SDK test run into one verdict and a report.

Usage: report.py <reports dir> --out <dir>
       report.py <reports dir> --current-passed

<reports dir> holds current-<language>.xml and, when the previous run ran,
previous-<language>.xml, as written by run_sdk_tests.sh.

Verdicts:
    OK     the generated SDK and the last released SDK both work with the app
    FLAG   the generated SDK works, the last released SDK does not
    BLOCK  the generated SDK does not work, or produced no results

--current-passed prints the languages whose current run passed, one per line;
the previous run starts only for those.

A skipped test counts as a failure: Speakeasy writes a skipped placeholder for a
case it could not turn into code, so a skip means the case never ran.
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


class Verdict(str, Enum):
    OK = "OK"
    FLAG = "FLAG"
    BLOCK = "BLOCK"


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


def read_junit(path: Path) -> dict[str, Case] | None:
    """Cases by ID, or None when the suite wrote no report."""
    if not path.is_file():
        return None
    cases: dict[str, Case] = {}
    for element in ET.parse(path).getroot().iter("testcase"):
        name = element.get("name", "")
        problem = element.find("failure")
        if problem is None:
            problem = element.find("error")
        if problem is not None:
            detail = (problem.get("message") or problem.text or "").strip()
            cases[case_id(name)] = Case(name, Status.FAILED, detail)
        elif element.find("skipped") is not None:
            cases[case_id(name)] = Case(
                name, Status.SKIPPED, "skipped: the case never ran"
            )
        else:
            cases[case_id(name)] = Case(name, Status.PASSED)
    return cases


def read_results(reports_dir: Path) -> list[LanguageResult]:
    return [
        LanguageResult(
            language,
            read_junit(reports_dir / f"{CURRENT}-{language}.xml"),
            read_junit(reports_dir / f"{PREVIOUS}-{language}.xml"),
        )
        for language in LANGUAGES
    ]


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


def render_summary(results: list[LanguageResult]) -> str:
    lines = [
        f"## SDK tests: {overall_verdict(results).value}",
        "",
        "| SDK | Verdict | Current run | Previous run |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r.language} | {r.verdict.value} | {_counts(r.current)} | {_counts(r.previous)} |"
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

    failures = [
        f"- **{result.language} {run}: {case.name}**: {case.detail.splitlines()[0][:300]}"
        for result in results
        for run, cases in (
            (CURRENT, result.current_failures),
            (PREVIOUS, result.previous_failures),
        )
        for case in cases
    ]
    if failures:
        lines += ["", "### Failures", "", *failures]
    return "\n".join(lines) + "\n"


def slack_message(results: list[LanguageResult], run_url: str) -> dict[str, object]:
    return {
        "verdict": overall_verdict(results).value,
        "text": f"SDK tests: {overall_verdict(results).value}",
        "run_url": run_url,
        "languages": [
            {
                "language": r.language,
                "verdict": r.verdict.value,
                "current": _counts(r.current),
                "previous": _counts(r.previous),
            }
            for r in results
        ],
        "thread": [
            f"{result.language} {run}: {case.name}"
            for result in results
            for run, cases in (
                (CURRENT, result.current_failures),
                (PREVIOUS, result.previous_failures),
            )
            for case in cases
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports_dir", type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--out", type=Path)
    output.add_argument("--current-passed", action="store_true")
    args = parser.parse_args()

    results = read_results(args.reports_dir)
    if args.current_passed:
        print(
            "\n".join(
                r.language for r in results if r.current and not r.current_failures
            )
        )
        return 0

    verdict = overall_verdict(results)
    summary = render_summary(results)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.md").write_text(summary, encoding="utf-8")
    (args.out / "verdict.txt").write_text(verdict.value + "\n", encoding="utf-8")
    (args.out / "slack.json").write_text(
        json.dumps(slack_message(results, os.getenv("SDK_TEST_RUN_URL", "")), indent=2)
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
