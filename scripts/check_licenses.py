#!/usr/bin/env python3
"""Fail when a dependency we link carries a licence we cannot ship under.

Reads the listings two common tools write and judges every package against a deny-list:

    --npm  NAME=FILE   `license-checker-rseidelsohn --json` (an object: "pkg@version" -> {"licenses": ...})
    --pip  NAME=FILE   `pip-licenses --format=json`         (a list: {"Name", "Version", "License"})

Denied families: AGPL, GPL, SSPL and BUSL (Business Source). LGPL is not GPL. A licence
expression is read as SPDX reads it: `A OR B` is denied only when every alternative is,
`A AND B` when any part is. A list a tool joined with `;` or `,` (several classifiers, or an
array in package.json) is treated like AND, because the tool does not say which one applies:
a person decides, and records the decision as an exception.

Scope: the packages our code links (the Node and Python backends and the frontend). A
service that runs as its own container is not a dependency in this sense and is not listed.

Exceptions live in a JSON file (`--exceptions`), one entry per reviewed package:

    {"ecosystem": "npm" | "pypi", "package": "name", "license": "<the licence text as listed>",
     "reason": "why it is acceptable", "reviewed": "YYYY-MM-DD", "expires": "YYYY-MM-DD"}

An entry covers that package only while its listed licence is exactly `license`, and only
until `expires`. An entry that is incomplete, expired or matches nothing fails the run, so
the file cannot rot into a list of things nobody looks at.

Exit status: 0 clean, 1 findings or a bad exceptions file, 2 unusable input.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DENIED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AGPL", re.compile(r"AGPL|AFFERO")),
    ("SSPL", re.compile(r"SSPL|SERVER SIDE PUBLIC")),
    ("BUSL", re.compile(r"BUSL|BUSINESS SOURCE")),
    # Not LGPL ("GNU Lesser/Library General Public License") and not AGPL, which is matched above.
    ("GPL", re.compile(r"(?<![A-Z])GPL|GNU GENERAL PUBLIC")),
)
ECOSYSTEMS = ("npm", "pypi")
EXCEPTION_KEYS = ("ecosystem", "package", "license", "reason", "reviewed", "expires")
UNKNOWN = ("", "UNKNOWN", "UNLICENSED", "CUSTOM", "SEE LICENSE IN LICENSE")


@dataclass(frozen=True)
class Package:
    ecosystem: str
    component: str
    name: str
    version: str
    license: str


@dataclass(frozen=True)
class Finding:
    package: Package
    families: tuple[str, ...]


class InputError(Exception):
    """A listing or the exceptions file cannot be read as what it should be."""


def _split_top(expression: str, separator: str) -> list[str]:
    """Split on ``separator`` outside parentheses."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(expression):
        char = expression[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and expression.startswith(separator, i):
            parts.append(expression[start:i])
            i += len(separator)
            start = i
            continue
        i += 1
    parts.append(expression[start:])
    return [part.strip() for part in parts if part.strip()]


def _unwrap(expression: str) -> str:
    expression = expression.strip()
    while expression.startswith("(") and expression.endswith(")") and _split_top(expression[1:-1], "\0") == [expression[1:-1].strip()]:
        depth = 0
        for index, char in enumerate(expression):
            depth += char == "("
            depth -= char == ")"
            if depth == 0 and index < len(expression) - 1:
                return expression  # the first parenthesis closes before the end: not one wrapping pair
        expression = expression[1:-1].strip()
    return expression


def denied_families(license_text: str) -> tuple[str, ...]:
    """The denied families a licence text puts the package under, empty when it can be shipped."""
    expression = _unwrap(re.sub(r"\s+", " ", license_text.upper()))
    for separator, every in ((" OR ", True), (" AND ", False), (";", False), (",", False)):
        parts = _split_top(expression, separator)
        if len(parts) > 1:
            found = [denied_families(part) for part in parts]
            if every and not all(found):
                return ()  # an alternative we may take
            return tuple(sorted({family for families in found for family in families}))
    # "GPL-2.0 WITH Classpath-exception-2.0" is still judged as GPL: an exception is for a person to review.
    return tuple(family for family, pattern in DENIED if pattern.search(expression))[:1]


def read_npm(component: str, path: Path) -> list[Package]:
    data = _json(path)
    if not isinstance(data, dict):
        raise InputError(f"{path}: expected the object license-checker --json writes")
    packages = []
    for key, entry in sorted(data.items()):
        name, _, version = key.rpartition("@")
        licenses = entry.get("licenses", "") if isinstance(entry, dict) else ""
        text = ", ".join(str(item) for item in licenses) if isinstance(licenses, list) else str(licenses)
        packages.append(Package("npm", component, name or key, version, text.strip()))
    return packages


def read_pip(component: str, path: Path) -> list[Package]:
    data = _json(path)
    if not isinstance(data, list):
        raise InputError(f"{path}: expected the list pip-licenses --format=json writes")
    packages = []
    for entry in data:
        if not isinstance(entry, dict) or "Name" not in entry:
            raise InputError(f"{path}: an entry has no Name")
        packages.append(
            Package("pypi", component, str(entry["Name"]), str(entry.get("Version", "")), str(entry.get("License", "")).strip())
        )
    return sorted(packages, key=lambda package: package.name.lower())


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"{path}: {error}") from error


def _canonical(ecosystem: str, name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "pypi" else name


def load_exceptions(path: Path | None, today: datetime.date) -> tuple[list[dict[str, str]], list[str]]:
    """The usable exceptions and what is wrong with the file."""
    if path is None:
        return [], []
    data = _json(path)
    entries = data.get("exceptions") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise InputError(f'{path}: expected {{"exceptions": [...]}}')
    usable, problems = [], []
    for index, entry in enumerate(entries):
        where = f"{path} exceptions[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: not an object")
            continue
        missing = [key for key in EXCEPTION_KEYS if not isinstance(entry.get(key), str) or not entry[key].strip()]
        if missing:
            problems.append(f"{where}: missing {', '.join(missing)}")
            continue
        if entry["ecosystem"] not in ECOSYSTEMS:
            problems.append(f"{where}: ecosystem must be one of {', '.join(ECOSYSTEMS)}")
            continue
        try:
            reviewed = datetime.date.fromisoformat(entry["reviewed"])
            expires = datetime.date.fromisoformat(entry["expires"])
        except ValueError:
            problems.append(f"{where}: reviewed and expires must be YYYY-MM-DD dates")
            continue
        if reviewed > today:
            problems.append(f"{where}: reviewed on {reviewed}, which has not happened yet")
            continue
        if expires < today:
            problems.append(f"{where}: {entry['package']} expired on {expires}; review it again or remove the dependency")
            continue
        usable.append(entry)
    return usable, problems


def judge(packages: list[Package], exceptions: list[dict[str, str]]) -> tuple[list[Finding], list[Finding], list[str]]:
    """(findings that fail the run, findings an exception covers, exceptions that covered nothing)."""
    failing, excepted, used = [], [], set()
    for package in packages:
        families = denied_families(package.license)
        if not families:
            continue
        finding = Finding(package, families)
        for index, entry in enumerate(exceptions):
            if (
                entry["ecosystem"] == package.ecosystem
                and _canonical(package.ecosystem, entry["package"]) == _canonical(package.ecosystem, package.name)
                and entry["license"].strip() == package.license
            ):
                used.add(index)
                excepted.append(finding)
                break
        else:
            failing.append(finding)
    unused = [
        f"{entry['ecosystem']} {entry['package']} ({entry['license']}): matches no listed package; remove the exception"
        for index, entry in enumerate(exceptions)
        if index not in used
    ]
    return failing, excepted, unused


def report(packages: list[Package], failing: list[Finding], excepted: list[Finding], problems: list[str]) -> str:
    lines = []
    for component in sorted({package.component for package in packages}):
        mine = [package for package in packages if package.component == component]
        counts = Counter(package.license or "(none stated)" for package in mine)
        lines.append(f"{component}: {len(mine)} packages")
        lines += [f"  {count:5d}  {licence}" for licence, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    unknown = [package for package in packages if package.license.upper() in UNKNOWN]
    if unknown:
        lines.append(f"no usable licence stated ({len(unknown)}; not judged, listed for review):")
        lines += [f"  {package.component}: {package.name}@{package.version}" for package in unknown]
    for title, findings in (("covered by a reviewed exception", excepted), ("DENIED", failing)):
        if findings:
            lines.append(f"{title} ({len(findings)}):")
            lines += [
                f"  {f.package.component}: {f.package.name}@{f.package.version}  {f.package.license}  [{', '.join(f.families)}]"
                for f in findings
            ]
    if problems:
        lines.append(f"exceptions file ({len(problems)}):")
        lines += [f"  {problem}" for problem in problems]
    lines.append("FAILED" if failing or problems else "OK: no denied licence among the listed packages")
    return "\n".join(lines)


def _listing(value: str) -> tuple[str, Path]:
    component, separator, path = value.partition("=")
    if not separator or not component or not path:
        raise argparse.ArgumentTypeError("expected NAME=FILE")
    return component, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--npm", action="append", default=[], type=_listing, metavar="NAME=FILE")
    parser.add_argument("--pip", action="append", default=[], type=_listing, metavar="NAME=FILE")
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--today", type=datetime.date.fromisoformat, default=None, help="for tests; default: today (UTC)")
    args = parser.parse_args(argv)
    if not args.npm and not args.pip:
        parser.error("give at least one --npm or --pip listing")
    today = args.today or datetime.datetime.now(datetime.timezone.utc).date()
    try:
        packages = [p for component, path in args.npm for p in read_npm(component, path)]
        packages += [p for component, path in args.pip for p in read_pip(component, path)]
        exceptions, problems = load_exceptions(args.exceptions, today)
    except InputError as error:
        print(f"check_licenses: {error}", file=sys.stderr)
        return 2
    empty = sorted({component for component, _ in (*args.npm, *args.pip)} - {package.component for package in packages})
    if empty:
        # A scan that listed nothing proves nothing: the tool ran in the wrong place or found no install.
        print(f"check_licenses: no package listed for {', '.join(empty)}", file=sys.stderr)
        return 2
    failing, excepted, unused = judge(packages, exceptions)
    print(report(packages, failing, excepted, problems + unused))
    return 1 if failing or problems or unused else 0


if __name__ == "__main__":
    sys.exit(main())
