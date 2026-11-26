"""``python -m app.control_plane.catalog.dump --write | --check``: the release catalog from the registries.

``--write`` rewrites the files that differ; ``--check`` exits 1 naming each stale file. Run from
``backend/python`` (the default paths are relative to the repository).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.control_plane.catalog.release import (
    build_release_catalog,
    render_node_module,
    render_release_files,
    stale_files,
)
from app.control_plane.catalog.release_sources import load_release_sources

_REPO = Path(__file__).resolve().parents[5]
DEFAULT_OUT = _REPO / "contracts/control-plane/catalog"
DEFAULT_NODE_OUT = _REPO / "backend/nodejs/apps/src/modules/control-plane/contracts/release-catalog.generated.ts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.control_plane.catalog.dump")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--node-out", type=Path, default=DEFAULT_NODE_OUT)
    args = parser.parse_args(argv)

    catalogs = build_release_catalog(load_release_sources())
    files = render_release_files(catalogs)
    node_file: Path = args.node_out
    targets = {**{args.out / name: text for name, text in files.items()}, node_file: render_node_module(catalogs)}
    stale = stale_files(targets)
    if args.check:
        for path in stale:
            sys.stderr.write(f"stale: {path} (run {parser.prog} --write)\n")
        return 1 if stale else 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(targets[path], encoding="utf-8")
    return 0


if __name__ == "__main__":
    logging.disable(logging.WARNING)
    sys.exit(main())
