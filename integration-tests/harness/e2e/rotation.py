"""Which stacks a scheduled run covers (04 §5): the nightly runs ST-1 and two more, the weekly every stack.

Each night pairs one Compose and one Helm profile with ST-1, so the seven stacks are covered every three nights and
twice a week, and a night's two extra stacks need one kind cluster at most.

    python rotation.py nightly 2026-10-11     # ST-1,ST-6,ST-5
    python rotation.py weekly                 # ST-1,...,ST-7
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime

import matrix

NIGHTLY_PAIRS = (("ST-2", "ST-3"), ("ST-6", "ST-5"), ("ST-7", "ST-4"))


def nightly(day: date) -> tuple[str, ...]:
    return ("ST-1", *NIGHTLY_PAIRS[day.toordinal() % len(NIGHTLY_PAIRS)])


def weekly() -> tuple[str, ...]:
    return tuple(sorted(matrix.STACKS))


def main(argv: list[str]) -> int:
    if argv[:1] == ["weekly"]:
        print(",".join(weekly()))  # noqa: T201 - the workflow reads it
        return 0
    if argv[:1] == ["nightly"]:
        print(",".join(nightly(  # noqa: T201 - the workflow reads it
            date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(UTC).date())))
        return 0
    print("usage: rotation.py nightly [YYYY-MM-DD] | weekly", file=sys.stderr)  # noqa: T201
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
