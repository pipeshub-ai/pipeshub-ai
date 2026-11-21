"""Read JUnit XML from any runner and map each testcase back to a catalog id."""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

PARAM = re.compile(r"\[.*\]$")
REQUIRE_STACK = re.compile(r"Require stack:\s*\n\s*-\s*(\S+)")


@dataclass
class Case:
    suite: str
    classname: str
    name: str
    file: str | None
    outcome: str  # pass | fail | error | skip
    seconds: float
    message: str


def read(path: Path) -> list[Case]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    cases = []
    for suite in root.iter("testsuite"):
        sname = suite.get("name", "")
        sfile = suite.get("file")
        for tc in suite.findall("testcase"):
            outcome, message = "pass", ""
            for tag in ("failure", "error", "skipped"):
                el = tc.find(tag)
                if el is not None:
                    outcome = {"failure": "fail", "error": "error", "skipped": "skip"}[tag]
                    message = (el.get("message") or "").strip()
                    lines = [line.strip() for line in (el.text or "").strip().splitlines() if line.strip()]
                    # pytest's collection errors say only "collection failure"; the cause is the last line.
                    if lines and message == "collection failure":
                        message = f"{message}: {lines[-1]}"
                    elif lines and not message:
                        message = lines[0]
                    message = message[:500]
                    break
            file = tc.get("file")
            if not file or file == "undefined":
                # mocha reports a spec that fails to load outside any suite; the require stack names it.
                m = REQUIRE_STACK.search(tc.findtext("failure") or tc.findtext("error") or "")
                file = m.group(1) if m else sfile
            cases.append(Case(
                suite=sname,
                classname=tc.get("classname", ""),
                name=tc.get("name", ""),
                file=file,
                outcome=outcome,
                seconds=float(tc.get("time") or 0),
                message=message,
            ))
    return cases


def worst(outcomes: list[str]) -> str:
    for o in ("error", "fail", "flaky", "pass", "skip"):
        if o in outcomes:
            return o
    return "skip"


def pytest_key(case: Case, modules: dict[str, str]) -> tuple[str | None, str | None]:
    """(file, "Class::test") for a pytest testcase, using the selected files' module names."""
    if not case.classname and case.name in modules:
        # A collection error: pytest reports the module path as the test name.
        return modules[case.name], "<collection>"
    parts = case.classname.split(".") if case.classname else []
    for cut in range(len(parts), 0, -1):
        dotted = ".".join(parts[:cut])
        if dotted in modules:
            rest = parts[cut:] + [PARAM.sub("", case.name)]
            return modules[dotted], "::".join(rest)
    if case.file and case.file in modules.values():
        return case.file, PARAM.sub("", case.name)
    return None, None


def module_names(files: list[str]) -> dict[str, str]:
    """Dotted module name (as pytest reports it) for each relative test file."""
    out = {}
    for f in files:
        stem = f.removesuffix(".py")
        parts = stem.split("/")
        # rootdir-relative and basename-relative forms both appear depending on rootdir/conftest layout
        for i in range(len(parts)):
            out.setdefault(".".join(parts[i:]), f)
    return out


def file_match(case: Case, rel_file: str) -> bool:
    base = posixpath.basename(rel_file)
    for field in (case.file, case.suite, case.classname):
        if field and (field.endswith(rel_file) or posixpath.basename(field) == base):
            return True
    return False
