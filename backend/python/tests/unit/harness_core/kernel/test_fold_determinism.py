"""QA13-12 (WRK-32, REPORT C2): the fold is the same under any hash seed.

A ``list(set)``-style ordering in the fold would change the folded state with ``PYTHONHASHSEED``, so a
worker would resume differently from the one that wrote the journal. Every kept journal (the goldens,
the replay corpus, its L4 runs) and every journal of the run machine's library is folded in fresh
interpreters under four seeds; the digests of the run and session state must all equal this process's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.harness_core.driver.journal_library import LIBRARY, generated_journal
from tests.unit.harness_core.durable.support import RUN, SESSION
from tests.unit.harness_core.kernel.fold_digest import fold_digest

PYTHON_ROOT = Path(__file__).resolve().parents[4]
VECTORS = PYTHON_ROOT.parents[1] / "contracts" / "harness" / "vectors"
SEEDS = ("0", "1", "2", "4242")
# Hand-written U3 screen fixtures (QA13-F1) fold too; their history is what is wrong, not their shape.
KEPT = sorted(
    [
        *VECTORS.glob("journals/*.journal.json"),
        *VECTORS.glob("replay-corpus/*/journals/*.journal.json"),
        *VECTORS.glob("replay-corpus/*/l4/*.journal.json"),
    ]
)


def _digests(paths: list[Path], seed: str) -> dict[str, str]:
    out = subprocess.run(
        [sys.executable, "-m", "tests.unit.harness_core.kernel.fold_digest", *map(str, paths)],
        cwd=PYTHON_ROOT,
        env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(PYTHON_ROOT)},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    return dict(line.rsplit(" ", 1) for line in out.splitlines())


@pytest.mark.timeout(300)
def test_qa13_12_every_journal_folds_to_one_state_under_every_hash_seed(tmp_path: Path) -> None:
    generated: list[Path] = []
    for name in LIBRARY:
        path = tmp_path / f"{name}.journal.json"
        events = [e.envelope() for e in generated_journal(name)]
        path.write_text(json.dumps({"runId": RUN, "sessionId": SESSION, "events": events}), encoding="utf-8")
        generated.append(path)
    paths = [*KEPT, *generated]
    here = {str(p): fold_digest(json.loads(p.read_text(encoding="utf-8"))) for p in paths}
    for seed in SEEDS:
        assert _digests(paths, seed) == here, f"PYTHONHASHSEED={seed}"
