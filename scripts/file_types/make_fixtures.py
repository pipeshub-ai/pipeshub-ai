#!/usr/bin/env python3
"""Write one real file of each type indexed since the second file-type registry, for a live check.

Copy the folder onto a share (SMB or any connector) and sync it: every file should end COMPLETED,
and a search for the sentinel (``manifest.json``) should find each one. The files are the ones the
unit tests read (``backend/python/tests/support/new_type_samples.py``).

Run from the repository root with the backend's Python:
    backend/python/venv/bin/python scripts/file_types/make_fixtures.py OUTDIR [--sentinel TEXT]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend" / "python"
# The .msg sample is a fixed upstream test file: its own text is what a search finds.
_MSG_TEXT = "Sicher gesendet mit Tutanota"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--sentinel", default="PipesHub new type check 4471")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(_BACKEND))
    from tests.support.new_type_samples import one_of_each

    args.outdir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for ext, content in one_of_each(args.sentinel).items():
        path = args.outdir / f"new-type-sample.{ext}"
        path.write_bytes(content)
        manifest[path.name] = {"bytes": len(content), "search_for": _MSG_TEXT if ext == "msg" else args.sentinel}
    (args.outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    sys.stdout.write(f"wrote {len(manifest)} files and manifest.json to {args.outdir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
