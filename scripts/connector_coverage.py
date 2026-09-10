#!/usr/bin/env python3
"""Report which shipped connectors have integration coverage, and gate on it.

    scripts/connector_coverage.py             # human-readable report
    scripts/connector_coverage.py --json      # machine-readable
    scripts/connector_coverage.py --check     # non-zero if a NEW gap appeared

Connectors break for reasons no unit test can catch: a provider changes a scope,
deprecates an endpoint, or alters a token-refresh response. The code did not
change, the world did. Only a test that talks to the real service notices, which
is what integration-tests/connectors/ is for — and today most connectors have
none.

The number matters less than the direction. `--check` compares against the
recorded baseline and fails when a connector is registered without a test, so
the gap can shrink over time but cannot silently widen. Update the baseline
deliberately, in a commit, when you add or remove a connector.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FACTORY = REPO / "backend/python/app/connectors/core/factory/connector_factory.py"
INTEGRATION = REPO / "integration-tests/connectors"
BASELINE = REPO / "scripts/connector_coverage_baseline.json"

# A registry key and its test directory rarely match character for character.
# Only genuinely different spellings belong here; anything else should be named
# consistently rather than aliased.
ALIASES = {
    "drive": {"google_drive_individual"},
    "driveworkspace": {"google_drive_workspace"},
    "postgresql": {"postgres"},
    "azureblob": {"azure_blob"},
    "azurefiles": {"azure_files"},
    "sharepointonline": {"sharepoint"},
    "confluencedatacenter": {"confluence"},
    "confluencedatacenterpersonal": {"confluence"},
    "jiracloudpersonal": {"jira"},
    "jiradatacenter": {"jira"},
    "jiradatacenterpersonal": {"jira"},
    "gmailworkspace": {"gmail"},
    "notionpersonal": {"notion"},
    "dropboxpersonal": {"dropbox"},
    "gitlabpersonal": {"gitlab"},
    "githubteams": {"github", "github_teams"},
    "slackworkspace": {"slack"},
    "outlookpersonal": {"outlook"},
}

# Not customer-facing connectors; excluded so the percentage means something.
NOT_A_CONNECTOR = {"kb"}


def registered_connectors() -> dict[str, str]:
    """Registry key -> class name, for the main and beta registries."""
    src = FACTORY.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for block in ("_connector_registry", "_beta_connector_definitions"):
        m = re.search(rf"{block}[^=]*=\s*\{{(.*?)\n    \}}", src, re.S)
        if not m:
            continue
        for key, cls in re.findall(r"""['"]([a-z0-9_]+)['"]\s*:\s*(\w+)""", m.group(1)):
            found[key] = cls
    return {k: v for k, v in found.items() if k not in NOT_A_CONNECTOR}


def tested_connectors() -> set[str]:
    """Directories under integration-tests/connectors that hold a test."""
    if not INTEGRATION.is_dir():
        return set()
    return {
        d.name
        for d in INTEGRATION.iterdir()
        # `any(a or b)` would silently ignore b: glob returns a generator, which
        # is truthy even when it matches nothing.
        if d.is_dir() and (any(d.glob("*_test.py")) or any(d.glob("test_*.py")))
    }


def covered(key: str, tests: set[str]) -> str | None:
    """The test directory covering this registry key, if any."""
    for candidate in {key, *ALIASES.get(key, set())}:
        if candidate in tests:
            return candidate
    return None


def build_report() -> dict:
    registry = registered_connectors()
    tests = tested_connectors()
    rows = []
    for key in sorted(registry):
        by = covered(key, tests)
        rows.append({"connector": key, "class": registry[key], "tested_by": by})
    have = [r for r in rows if r["tested_by"]]
    orphan = sorted(tests - {r["tested_by"] for r in have if r["tested_by"]})
    return {
        "total": len(rows),
        "covered": len(have),
        "uncovered": len(rows) - len(have),
        "percent": round(100 * len(have) / len(rows), 1) if rows else 0.0,
        "connectors": rows,
        "test_dirs_without_a_registered_connector": orphan,
    }


def print_report(rep: dict) -> None:
    print(f"Connector integration coverage: {rep['covered']}/{rep['total']} ({rep['percent']}%)")
    print()
    have = [r for r in rep["connectors"] if r["tested_by"]]
    miss = [r for r in rep["connectors"] if not r["tested_by"]]
    if have:
        print(f"  Covered ({len(have)}):")
        for r in have:
            suffix = "" if r["tested_by"] == r["connector"] else f"  -> {r['tested_by']}"
            print(f"    {r['connector']}{suffix}")
        print()
    if miss:
        print(f"  No integration test ({len(miss)}):")
        for r in miss:
            print(f"    {r['connector']}")
        print()
    if rep["test_dirs_without_a_registered_connector"]:
        print("  Test directories with no registered connector:")
        for d in rep["test_dirs_without_a_registered_connector"]:
            print(f"    {d}")
        print()


def check(rep: dict) -> int:
    """Fail when a connector is registered without a test that was not there before."""
    if not BASELINE.exists():
        print(f"No baseline at {BASELINE.relative_to(REPO)}. Create it with:")
        print("  scripts/connector_coverage.py --write-baseline")
        return 1

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    known = set(baseline.get("uncovered_connectors", []))
    now = {r["connector"] for r in rep["connectors"] if not r["tested_by"]}

    new_gaps = sorted(now - known)
    closed = sorted(known - now)

    if closed:
        print("Coverage improved — these connectors now have integration tests:")
        for c in closed:
            print(f"  + {c}")
        print()
        print("Record it:  scripts/connector_coverage.py --write-baseline")
        print()

    if new_gaps:
        print("FAIL: these connectors are registered but have no integration test:")
        for c in new_gaps:
            print(f"  - {c}")
        print()
        print("A connector without one breaks when the provider changes, and nothing")
        print("notices until a user reports it. Add a test under")
        print("integration-tests/connectors/, or record the gap deliberately with")
        print("  scripts/connector_coverage.py --write-baseline")
        return 1

    print(f"OK: no new uncovered connectors ({rep['covered']}/{rep['total']} covered).")
    return 0


def write_baseline(rep: dict) -> int:
    payload = {
        "_comment": (
            "Connectors knowingly shipping without integration coverage. "
            "--check fails when a connector appears that is not listed here, so "
            "this list may shrink freely but should only grow deliberately."
        ),
        "covered": rep["covered"],
        "total": rep["total"],
        "uncovered_connectors": sorted(
            r["connector"] for r in rep["connectors"] if not r["tested_by"]
        ),
    }
    BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {BASELINE.relative_to(REPO)} ({rep['covered']}/{rep['total']} covered)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check", action="store_true", help="fail if a new gap appeared")
    ap.add_argument("--write-baseline", action="store_true", help="record the current gaps")
    args = ap.parse_args()

    if not FACTORY.exists():
        print(f"cannot find {FACTORY}", file=sys.stderr)
        return 2

    rep = build_report()
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0
    if args.write_baseline:
        return write_baseline(rep)
    if args.check:
        return check(rep)
    print_report(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
