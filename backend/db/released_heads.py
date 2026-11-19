"""Record the tree's migration heads as a release's in ``released_heads.json`` (README.md, Released heads).

    python -m db.released_heads v1.2.0      # from backend/, on the commit to be tagged
    python -m db.released_heads --check     # exit 1 when the file's epochs are not the tree's heads
"""
import json
import sys
from pathlib import Path

from db import epoch_lint, locations

FILE = locations.DB_DIR / "released_heads.json"


def tree_epochs() -> dict[str, int]:
    heads, _ = epoch_lint.expected_heads(locations.version_locations())
    return {branch: epoch for branch, (epoch, _) in sorted(heads.items())}


def write(release: str, path: Path = FILE) -> None:
    path.write_text(json.dumps({"release": release, "epochs": tree_epochs()}, indent=2) + "\n")


def check(path: Path = FILE) -> str:
    """The mismatch, or "" when the file's epochs are the tree's heads. Epochs only: a pre-release or a hotfix tag ships
    the heads of the release the file names."""
    recorded, now = json.loads(path.read_text())["epochs"], tree_epochs()
    return "" if recorded == now else f"{path.name} records {recorded}, the tree's heads are {now}"


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check() or 0)  # a message exits 1
    if len(sys.argv) != 2 or not sys.argv[1].startswith("v"):
        sys.exit("usage: python -m db.released_heads <tag, e.g. v1.2.0> | --check")
    write(sys.argv[1])
