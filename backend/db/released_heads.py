"""Record the tree's migration heads as a release's in ``released_heads.json`` (README.md, Released heads).

    python -m db.released_heads v1.2.0      # from backend/, on the commit to be tagged
"""
import json
import sys
from pathlib import Path

from db import epoch_lint, locations

FILE = locations.DB_DIR / "released_heads.json"


def write(release: str, path: Path = FILE) -> None:
    heads, _ = epoch_lint.expected_heads([Path(p) for p in locations.version_locations()])
    epochs = {branch: epoch for branch, (epoch, _) in sorted(heads.items())}
    path.write_text(json.dumps({"release": release, "epochs": epochs}, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("v"):
        sys.exit("usage: python -m db.released_heads <tag, e.g. v1.2.0>")
    write(sys.argv[1])
