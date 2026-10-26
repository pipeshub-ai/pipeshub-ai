#!/usr/bin/env python3
"""Fail when a JUnit/xunit report (pytest --junitxml, mocha --reporter xunit) holds a skipped test that is not allow-listed.

A suite that "passed" with its store down had skipped every store-backed test; the workflows that start the services run this
after the tests. usage: assert_no_skips.py REPORT.xml [REPORT.xml ...] [--allow SUBSTRING ...] [--min-tests N]
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET


def skipped(paths: list[str]) -> tuple[int, list[str]]:
    total, skips = 0, []
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            total += 1
            if case.find("skipped") is not None:
                skips.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return total, skips


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--allow", action="append", default=[], help="substring of a skipped test id that may be skipped")
    ap.add_argument("--min-tests", type=int, default=1, help="fail when fewer tests than this ran (an empty run is not a pass)")
    args = ap.parse_args(argv)
    total, skips = skipped(args.reports)
    bad = [s for s in skips if not any(a in s for a in args.allow)]
    print(f"{total} tests, {len(skips)} skipped, {len(skips) - len(bad)} allow-listed")
    for s in bad:
        print(f"UNEXPECTED SKIP {s}")
    if total < args.min_tests:
        print(f"only {total} tests ran, expected at least {args.min_tests}")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
