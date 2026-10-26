"""Write schema_requirements.json for a build artifact:
``python -m db.schema_requirements identity,access > schema_requirements.json``.
Each listed branch gets ``{"requires": <newest epoch in the tree>, "code": <the same>}`` (the artifact is
built from this tree). Boot checks: ``boot_assert.schema_problems`` (Python), ``libs/db/schema-assert.ts`` (Node)."""
import json
import re
import sys
from pathlib import Path

from db.locations import version_locations


def heads() -> dict[str, int]:
    """The newest epoch of every branch in this tree."""
    out = {}
    for loc in version_locations():
        epochs = [int(m[1]) for f in Path(loc).glob("*.py") if (m := re.match(r"(\d{4})_", f.name))]
        if epochs:
            out[Path(loc).name] = max(epochs)
    return out


def requirements(branches: list[str]) -> dict[str, dict[str, int]]:
    heads_ = heads()
    unknown = [b for b in branches if b not in heads_]
    if unknown:
        raise SystemExit(f"unknown branch(es): {', '.join(unknown)}")
    return {b: {"requires": heads_[b], "code": heads_[b]} for b in branches}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("usage: python -m db.schema_requirements <branch>[,<branch>...]\n")
        return 2
    print(json.dumps(requirements([b for b in argv[0].split(",") if b]), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
