"""Fails the storage job when a test did not run, from the junit report.

Environment: REPORT (junit file), DISCOVERED (file listing the test files the job
discovered), STORAGE_TYPE (the store this leg runs: local, s3 or azureBlob).

The job fails when
- nothing ran, or a test was skipped, or
- a discovered file contributed no test case (for example it lacks
  ``pytest.mark.integration``, so pytest deselected all of it).

One skip is allowed: a test that cannot apply to this store says so with a reason
beginning ``not applicable on <store>:``, and it counts only on the leg running
that store. The same reason on any other leg, or any other skip, fails.
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET

NOT_APPLICABLE = re.compile(r"^not applicable on (\w+): ")


def main() -> int:
    store = os.environ["STORAGE_TYPE"]
    cases = list(ET.parse(os.environ["REPORT"]).getroot().iter("testcase"))
    with open(os.environ["DISCOVERED"], encoding="utf-8") as listing:
        modules = [f.removesuffix(".py").replace("/", ".") for f in listing.read().split()]

    skipped = 0
    for case in cases:
        for skip in case.findall("skipped"):
            # A strict xfail ran (a known gap, documented in its marker); junit files it as a skip.
            if skip.get("type") == "pytest.xfail":
                continue
            named = NOT_APPLICABLE.match(skip.get("message") or "")
            if not (named and named.group(1) == store):
                skipped += 1

    silent = [
        m for m in modules
        if not any(c.get("classname", "") == m or c.get("classname", "").startswith(m + ".") for c in cases)
    ]
    if silent:
        print(f"::error::No test case ran from {', '.join(silent)}. Does each test carry pytest.mark.integration?")
        return 1
    if not cases or skipped:
        print(
            f"::error::{len(cases)} collected, {skipped} skipped. Each skip is a test that did not run "
            "against the storage service: it was unreachable, or the test could not set up what it "
            f"checks. The only skip allowed is one whose reason begins 'not applicable on {store}:'. "
            "The reasons are in the test step above."
        )
        return 1
    print(f"{len(cases)} tests ran against a real storage service, none skipped without cause.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
