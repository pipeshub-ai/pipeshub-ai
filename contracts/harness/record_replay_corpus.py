"""Freeze a replay corpus: what one release wrote, for the next release to read (P1b RU-3).

A corpus is a directory under ``vectors/replay-corpus/<name>/``:

- ``journals/``: the golden journals with the parts Node projected from them;
- ``l4/``: journals of real-process runs, as Node stored them (optional);
- ``pinned-run-spec.json``: the golden pin;
- ``checkpoint.json``: a checkpoint of one golden journal, written by this build's fold;
- ``manifest.json``: the fold and spec versions that wrote it, and what it holds.

The replay-compat tests (``backend/python/tests/contracts/test_replay_compat.py``,
``backend/nodejs/apps/tests/contracts/replay-compat.test.ts``) read every corpus
on every build. Record one per release, before the release that could break it:

    backend/python/.venv/bin/python contracts/harness/record_replay_corpus.py <name> \\
        [--l4 <fault-suite artifacts dir>] [--from-ref <git ref>] [--out <dir>]

``--l4`` adds the journals the L4 fault suite saved (``<broker>/<scenario>/journal.json``).
``--from-ref`` records another build's goldens and pin from git instead of the
working tree (CI replays the previous release's this way); it writes no
checkpoint, since only that build's fold could.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VECTORS = HERE / "vectors"
JOURNALS = VECTORS / "journals"
PIN = VECTORS / "pinned-run-spec.json"
CORPORA = VECTORS / "replay-corpus"
FOLD_SOURCE = "backend/python/harness_core/kernel/fold.py"
RUN_SPEC_SOURCE = "backend/nodejs/apps/src/modules/agent-harness/domain/run-spec.ts"
# The golden journal whose fold is frozen as the corpus checkpoint: a tool turn, so the state is not trivial.
CHECKPOINT_JOURNAL = "agent_quick_tool_call"


def _git_show(ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "show", f"{ref}:{path}"], check=True, capture_output=True, text=True
    ).stdout


def _git_ls(ref: str, path: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-tree", "--name-only", f"{ref}:{path}"], check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line]


def _constant(source: str, pattern: str) -> int:
    match = re.search(pattern, source, re.MULTILINE)
    if match is None:
        raise SystemExit(f"constant {pattern!r} not found")
    return int(match.group(1))


def _versions(fold_source: str, run_spec_source: str) -> dict[str, int]:
    return {
        "foldVersion": _constant(fold_source, r"^FOLD_VERSION = (\d+)"),
        "eventSchemaVersion": _constant(fold_source, r"^SUPPORTED_SCHEMA_VERSION = (\d+)"),
        "runSpecSchemaVersion": _constant(run_spec_source, r"^export const RUN_SPEC_SCHEMA_VERSION = (\d+);"),
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_sources(ref: str | None, out: Path) -> tuple[list[str], dict[str, int]]:
    """The goldens and the pin, from the working tree or from ``ref``; the versions that wrote them."""
    if ref is None:
        names = sorted(p.name for p in JOURNALS.glob("*.json"))
        for name in names:
            shutil.copyfile(JOURNALS / name, out / "journals" / name)
        shutil.copyfile(PIN, out / "pinned-run-spec.json")
        versions = _versions((REPO / FOLD_SOURCE).read_text(), (REPO / RUN_SPEC_SOURCE).read_text())
    else:
        journals = JOURNALS.relative_to(REPO).as_posix()
        names = sorted(n for n in _git_ls(ref, journals) if n.endswith(".json"))
        for name in names:
            _write(out / "journals" / name, _git_show(ref, f"{journals}/{name}"))
        _write(out / "pinned-run-spec.json", _git_show(ref, PIN.relative_to(REPO).as_posix()))
        versions = _versions(_git_show(ref, FOLD_SOURCE), _git_show(ref, RUN_SPEC_SOURCE))
    scenarios = sorted({n.split(".", 1)[0] for n in names if n.endswith(".journal.json")})
    return scenarios, versions


def _l4_journals(artifacts: Path, out: Path) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []
    for path in sorted(artifacts.glob("*/*/journal.json")):
        doc = json.loads(path.read_text())
        name = f"{doc['broker']}-{doc['scenario']}"
        _write(out / "l4" / f"{name}.journal.json", json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        recorded.append({"name": name, "status": doc["status"]})
    return recorded


def _checkpoint(out: Path) -> None:
    """This build's fold of one golden journal, as a worker would save it at the journal's end."""
    sys.path.insert(0, str(REPO / "backend" / "python"))
    from harness_core.journal import JournalEvent
    from harness_core.kernel.fold import fold_run, fold_session, make_checkpoint

    doc = json.loads((out / "journals" / f"{CHECKPOINT_JOURNAL}.journal.json").read_text())
    events = [JournalEvent.from_envelope(raw) for raw in doc["events"]]
    run = fold_run(events, run_id=doc["runId"])
    session = fold_session(events, stream_id=doc["sessionId"])
    checkpoint = make_checkpoint(run, session)
    body = {"journal": CHECKPOINT_JOURNAL, "checkpoint": checkpoint.model_dump(mode="json")}
    _write(out / "checkpoint.json", json.dumps(body, indent=1, ensure_ascii=False) + "\n")


def record(name: str, *, l4: Path | None, ref: str | None, root: Path) -> Path:
    out = root / name
    if out.exists():
        raise SystemExit(f"{out} exists: a corpus is frozen once recorded")
    (out / "journals").mkdir(parents=True)
    scenarios, versions = _copy_sources(ref, out)
    runs = _l4_journals(l4, out) if l4 is not None else []
    if ref is None:
        _checkpoint(out)
    manifest = {
        "name": name,
        "recordedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gitRef": ref
        or subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        .stdout.strip(),
        **versions,
        "journals": scenarios,
        "l4": runs,
        "checkpoint": CHECKPOINT_JOURNAL if ref is None else None,
    }
    _write(out / "manifest.json", json.dumps(manifest, indent=1) + "\n")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="the corpus name, e.g. the release that wrote it")
    parser.add_argument("--l4", type=Path, help="L4 fault-suite artifacts to add (its <broker>/<scenario>/journal.json)")
    parser.add_argument("--from-ref", help="record the goldens and pin of this git ref instead of the working tree")
    parser.add_argument("--out", type=Path, default=CORPORA, help=f"where corpora live (default {CORPORA})")
    args = parser.parse_args()
    sys.stdout.write(f"{record(args.name, l4=args.l4, ref=args.from_ref, root=args.out)}\n")


if __name__ == "__main__":
    main()
