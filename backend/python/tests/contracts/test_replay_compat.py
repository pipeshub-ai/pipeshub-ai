"""N-1 read guarantee (P1b RU-3): what an earlier release wrote, this build reads.

Every corpus under ``contracts/harness/vectors/replay-corpus/`` (recorded by
``contracts/harness/record_replay_corpus.py``, one per release) is replayed on
this build: each journal folds without ``FoldIncompatible`` and to the parts
Node projected when it was recorded, each L4 run folds to the status Node
stored, the pin validates as this worker's ``RunSpec``, and a checkpoint is
restored only by the fold version that wrote it (any other folds in full).
The Node half is ``backend/nodejs/apps/tests/contracts/replay-compat.test.ts``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.harness.contracts.state import PinnedRunSpec
from harness_core.contracts import canonical_digest
from harness_core.journal import Checkpoint, JournalEvent
from harness_core.kernel.fold import (
    FOLD_VERSION,
    checkpoint_is_valid,
    fold_run,
    fold_session,
    restore_checkpoint,
)
from tests.contracts.test_durable_journals import fold_skeleton, projector_skeleton

# Node's run statuses that the fold names otherwise.
FOLD_STATUS = {"waiting_approval": "suspended", "paused": "suspended"}
CORPORA = Path(__file__).resolve().parents[4] / "contracts" / "harness" / "vectors" / "replay-corpus"


@dataclass(frozen=True)
class Corpus:
    path: Path
    manifest: dict[str, Any]

    @property
    def name(self) -> str:
        return self.path.name

    def read(self, relative: str) -> dict[str, Any]:
        return json.loads((self.path / relative).read_text(encoding="utf-8"))


def _corpora() -> list[Corpus]:
    return [
        Corpus(manifest.parent, json.loads(manifest.read_text(encoding="utf-8")))
        for manifest in sorted(CORPORA.glob("*/manifest.json"))
    ]


CORPUS_LIST = _corpora()
JOURNALS = [(c, name) for c in CORPUS_LIST for name in c.manifest["journals"]]
L4_RUNS = [(c, run["name"], run["status"]) for c in CORPUS_LIST for run in c.manifest["l4"]]


def _events(doc: dict[str, Any]) -> list[JournalEvent]:
    return [JournalEvent.from_envelope(raw) for raw in doc["events"]]


def test_there_is_a_corpus_to_replay() -> None:
    assert CORPUS_LIST, f"no replay corpus under {CORPORA}; record one with record_replay_corpus.py"


@pytest.mark.parametrize(("corpus", "name"), JOURNALS, ids=[f"{c.name}/{n}" for c, n in JOURNALS])
def test_ru3b_a_recorded_journal_folds_to_the_parts_recorded_with_it(corpus: Corpus, name: str) -> None:
    doc = corpus.read(f"journals/{name}.journal.json")
    events = _events(doc)
    fold_session(events, stream_id=doc["sessionId"])
    fold_run(events, run_id=doc["runId"])
    parts_file = corpus.path / "journals" / f"{name}.parts.json"
    if not parts_file.exists():
        return
    projected = projector_skeleton(json.loads(parts_file.read_text(encoding="utf-8"))["parts"])
    fold = fold_skeleton(doc)
    # A final answer the finalizer appended (no model text part) has no fold entry.
    if len(projected) == len(fold) + 1 and projected[-1] == ("text",):
        projected = projected[:-1]
    assert [item[:1] if item[0] == "text" else item for item in fold] == projected


@pytest.mark.parametrize(("corpus", "name", "status"), L4_RUNS, ids=[f"{c.name}/{n}" for c, n, _ in L4_RUNS])
def test_ru3b_a_real_run_folds_to_the_status_node_stored(corpus: Corpus, name: str, status: str) -> None:
    doc = corpus.read(f"l4/{name}.journal.json")
    events = _events(doc)
    fold_session(events, stream_id=doc["sessionId"])
    assert fold_run(events, run_id=doc["runId"]).status == FOLD_STATUS.get(status, status)


@pytest.mark.parametrize("corpus", CORPUS_LIST, ids=[c.name for c in CORPUS_LIST])
def test_ru3b_the_recorded_pin_validates_as_this_workers_run_spec(corpus: Corpus) -> None:
    pinned = corpus.read("pinned-run-spec.json")["pinned"]
    spec = PinnedRunSpec.model_validate(pinned).run_spec()
    assert spec.schema_version == pinned["spec"]["schemaVersion"]
    assert canonical_digest(pinned["spec"]) == pinned["specDigest"]


@pytest.mark.parametrize(
    "corpus",
    [c for c in CORPUS_LIST if c.manifest["checkpoint"] is not None],
    ids=[c.name for c in CORPUS_LIST if c.manifest["checkpoint"] is not None],
)
def test_ru3b_a_checkpoint_is_restored_only_by_the_fold_that_wrote_it(corpus: Corpus) -> None:
    recorded = corpus.read("checkpoint.json")
    checkpoint = Checkpoint.model_validate(recorded["checkpoint"])
    doc = corpus.read(f"journals/{recorded['journal']}.journal.json")
    events = _events(doc)
    usable = checkpoint_is_valid(
        checkpoint, run_id=doc["runId"], head_seq=events[-1].seq, ref="ref", referenced_ref="ref"
    )
    assert usable == (checkpoint.fold_version == FOLD_VERSION)
    full_run = fold_run(events, run_id=doc["runId"])
    full_session = fold_session(events, stream_id=doc["sessionId"])
    if usable:
        run, session = restore_checkpoint(checkpoint)
        assert run == full_run
        assert session == full_session
