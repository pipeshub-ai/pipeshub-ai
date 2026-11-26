"""Compare two commit traces written by ``plugin.py``: ``python -m tests.support.commit_trace.diff A B``."""

from __future__ import annotations

import base64
import collections
import json
import re
import sys
from pathlib import Path

# ask_user_question draws a uuid4, so its inline result blobs differ run to run.
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


def _norm(value: object) -> object:
    if isinstance(value, dict):
        ref = value.get("ref")
        if isinstance(ref, str) and ref.startswith("inline:"):
            raw = base64.urlsafe_b64decode(ref[7:] + "==").decode()
            return {"inline": _UUID.sub("UUID", raw), "size": value.get("size")}
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return _UUID.sub("UUID", value)
    return value


def load(directory: str) -> dict[str, list[dict[str, object]]]:
    traces: dict[str, list[dict[str, object]]] = {}
    for path in sorted(Path(directory).glob("*.jsonl")):
        for line in path.read_text().splitlines():
            record = _norm(json.loads(line))
            assert isinstance(record, dict)
            traces[str(record["test"])] = record["commits"]  # type: ignore[assignment]
    traces.pop("", None)
    return traces


def main(base_dir: str, branch_dir: str) -> int:
    base, branch = load(base_dir), load(branch_dir)
    shared = sorted(set(base) & set(branch))
    differing = [t for t in shared if base[t] != branch[t]]
    commits = sum(len(base[t]) for t in shared)
    events = sum(len(c["events"]) for t in shared for c in base[t])  # type: ignore[arg-type]
    kinds = collections.Counter(
        [*str(c["command_id"]).split(":"), "?"][1] for t in shared for c in base[t]
    )
    print(
        f"tests compared: {len(shared)}; identical: {len(shared) - len(differing)}; differing: {len(differing)}"
    )
    print(
        f"only in base: {len(set(base) - set(branch))}; only in branch: {len(set(branch) - set(base))}"
    )
    print(f"commit requests: {commits}; events: {events}")
    print("commit kinds:", dict(sorted(kinds.items())))
    for test in differing:
        print("DIFF", test)
    return 1 if differing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
