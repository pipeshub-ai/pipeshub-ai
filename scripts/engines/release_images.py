#!/usr/bin/env python3
"""The engine images the release gate builds, and the check that their real-lane cases ran (C6.42).

    python3 scripts/engines/release_images.py plan               # one line per build: KIND [VARIANT]
    python3 scripts/engines/release_images.py build              # builds every planned image with engine-build.sh
    python3 scripts/engines/release_images.py check REPORT.xml   # red when a case skipped for want of a built image

Every engine the allowlist pins that PipesHub may redistribute is built from its pinned release, the run image (the
tools variant, `ph` included) and the engine-only image its tools-layer licence scan compares against. An entry whose
``supply`` is ``admin`` (Claude Code, D29) has no asset to build from on a runner: its cases may skip. A second kind
that shares another's image (acp-opencode runs opencode's) is built once, under the kind that owns the image folder.

``check`` reads the pytest JUnit report of the engine lane. A skip whose reason names an image this plan built, or the
build script of one, means the gate ran without the case it exists for: a skip of that kind once let C-13 read green.
Skips for other reasons (a live lane's variables, an admin-built engine) pass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = REPO / "backend" / "python" / "harness_engines" / "allowlist.json"
IMAGES = REPO / "deployment" / "sandbox-images"


@dataclass(frozen=True)
class Build:
    kind: str
    folder: str
    tag: str
    variant: str | None = None

    def command(self) -> list[str]:
        cmd = ["sh", str(IMAGES / "engine-build.sh"), self.kind]
        return [*cmd, "--variant", self.variant] if self.variant else cmd


def _default_entries(allowlist: dict) -> list[dict]:
    """Each kind's default version: its first entry (C14.01), which the lanes run."""
    seen: dict[str, dict] = {}
    for entry in allowlist["engines"]:
        seen.setdefault(entry["kind"], entry)
    return list(seen.values())


def plan(allowlist: dict, images: Path = IMAGES) -> list[Build]:
    builds: list[Build] = []
    for entry in _default_entries(allowlist):
        folder = entry["kind"].replace("_", "-")
        if entry.get("supply") == "admin" or not (images / folder / "Dockerfile").is_file():
            continue
        builds.append(Build(entry["kind"], folder, entry["image"]))
        engine_only = (entry.get("variants") or {}).get("engine")
        if engine_only:
            builds.append(Build(entry["kind"], folder, engine_only, "engine"))
    return builds


def _skips(report: Path) -> list[tuple[str, str]]:
    out = []
    for case in ET.parse(report).getroot().iter("testcase"):
        skipped = case.find("skipped")
        if skipped is not None:
            reason = " ".join(filter(None, (skipped.get("message"), skipped.text)))
            out.append((f"{case.get('classname', '')}::{case.get('name', '')}", reason))
    return out


def hidden_cases(builds: list[Build], reports: list[Path]) -> list[str]:
    """The skipped cases whose reason names a built image or its folder's build script."""
    markers = {b.tag for b in builds} | {f"sandbox-images/{b.folder}/build.sh" for b in builds}
    markers |= {f"the {b.kind} image" for b in builds}
    return [f"{case}: {reason}" for report in reports for case, reason in _skips(report)
            if any(marker in reason for marker in markers)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("build")
    check = sub.add_parser("check")
    check.add_argument("reports", nargs="+", type=Path)
    check.add_argument("--min-tests", type=int, default=1)
    args = ap.parse_args(argv)
    builds = plan(json.loads(ALLOWLIST.read_text(encoding="utf-8")))
    if args.cmd == "plan":
        for b in builds:
            print(b.kind, b.variant or "", b.tag)
        return 0
    if args.cmd == "build":
        for b in builds:
            print(f"::group::{b.tag}", flush=True)
            subprocess.run(b.command(), check=True)
            print("::endgroup::", flush=True)
        return 0
    total = sum(1 for r in args.reports for _ in ET.parse(r).getroot().iter("testcase"))
    hidden = hidden_cases(builds, args.reports)
    for line in hidden:
        print(f"SKIPPED FOR WANT OF A BUILT IMAGE {line}")
    print(f"{total} tests, {len(hidden)} skipped although their image was built")
    if total < args.min_tests:
        print(f"only {total} tests ran, expected at least {args.min_tests}")
        return 1
    return 1 if hidden else 0


if __name__ == "__main__":
    sys.exit(main())
