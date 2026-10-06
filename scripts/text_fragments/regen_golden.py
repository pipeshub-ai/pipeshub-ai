#!/usr/bin/env python3
"""Refresh or verify the text-fragment golden corpus.

    python scripts/text_fragments/regen_golden.py             # report drift, exit 1 if any
    python scripts/text_fragments/regen_golden.py --update    # rewrite expected_url snapshots
    python scripts/text_fragments/regen_golden.py --bootstrap # also fill missing expected_highlight

`expected_url` is a snapshot of generator output. `expected_highlight` is the
independent oracle, so it is only ever written for cases that have none yet
(`--bootstrap`); read the printed highlight and confirm it is what a person
would expect to see marked on the page before committing it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_PYTHON = Path(__file__).resolve().parents[2] / "backend" / "python"
sys.path.insert(0, str(BACKEND_PYTHON))

from tests.support.text_fragment_corpus import (  # noqa: E402
    CASES_FILE,
    dump_cases,
    generate_url,
    highlight_of,
    load_cases,
    load_raw_cases,
)


def say(message: str) -> None:
    sys.stdout.write(message + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="rewrite expected_url snapshots")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="like --update, and also fill expected_highlight where it is null",
    )
    args = parser.parse_args()
    rewrite_urls = args.update or args.bootstrap

    raw_cases = load_raw_cases()
    changed = False
    problems = 0

    for case, raw in zip(load_cases(), raw_cases, strict=True):
        url = generate_url(case)
        highlight = highlight_of(case, url)

        if raw.get("expected_url") != url:
            say(f"[url]       {case.id}\n    was: {raw.get('expected_url')}\n    now: {url}")
            if rewrite_urls:
                raw["expected_url"] = url
                changed = True
            else:
                problems += 1

        if raw.get("expected_highlight") is None and highlight is not None:
            say(f"[highlight] {case.id}: {highlight!r}")
            if args.bootstrap:
                raw["expected_highlight"] = highlight
                changed = True
            else:
                problems += 1
        elif raw.get("expected_highlight") != highlight:
            say(
                f"[MISMATCH]  {case.id}\n"
                f"    expected: {raw.get('expected_highlight')!r}\n"
                f"    matcher:  {highlight!r}"
            )
            problems += 1

    if changed:
        CASES_FILE.write_text(dump_cases(raw_cases), encoding="utf-8")
        say(f"wrote {CASES_FILE}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
