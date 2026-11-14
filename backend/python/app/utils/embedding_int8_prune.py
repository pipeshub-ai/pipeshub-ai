"""Remove the int8 weight files of old model revisions from the model cache, by hand.

The server never deletes these files: a cache volume can be shared by replicas, or by an old image across a
format change, and a process cannot know that no one else has a file mapped. Run this only when nothing
else uses the cache. File names are ``<model>-<12 hex revision>-<16 hex hash>-v<format>.safetensors``.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from app.utils.int8_file import read_header

_NAME = re.compile(r"^(?P<model>.+)-(?P<rev>[0-9a-f]{12})-(?P<sha>[0-9a-f]{16})-v(?P<fmt>\d+)\.safetensors$")
MIN_AGE_SECONDS = 30 * 60


def parse_name(name: str) -> tuple[str, str, str, str] | None:
    """(model part, revision, hash, format) of a file name, or None for anything else."""
    match = _NAME.match(name)
    return (match["model"], match["rev"], match["sha"], match["fmt"]) if match else None


def _stored_model(path: Path) -> str | None:
    try:
        header, _ = read_header(path)
        import json

        return json.loads((header.get("__metadata__") or {}).get("identity", "{}")).get("model")
    except Exception:
        return None


def stale_files(cache_dir: Path, model: str, keep: Path, *, now: float | None = None) -> list[Path]:
    """Files of the same model and format as *keep* but another revision or hash, old enough to be unused.

    The model is compared exactly, twice: the name part must equal the sanitised id, and the id stored
    inside the file must equal *model*, so ``bge-small-en`` never matches ``bge-small-en-v1.5`` and
    ``a/b`` never matches ``a_b``.
    """
    now = time.time() if now is None else now
    wanted = parse_name(keep.name)
    if wanted is None:
        return []
    out: list[Path] = []
    for path in sorted(cache_dir.glob("*.safetensors")):
        parsed = parse_name(path.name)
        if parsed is None or path == keep:
            continue
        model_part, rev, sha, fmt = parsed
        if model_part != wanted[0] or fmt != wanted[3] or (rev, sha) == (wanted[1], wanted[2]):
            continue
        if now - path.stat().st_mtime < MIN_AGE_SECONDS or _stored_model(path) != model:
            continue
        out.append(path)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: python -m app.utils.embedding_int8_prune <cache dir> <model id> <file to keep>\nIt only lists the files; delete them yourself.")  # noqa: T201
        return 2
    cache, model, keep = Path(argv[1]), argv[2], Path(argv[3])
    for path in stale_files(cache, model, keep):
        print(path)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
