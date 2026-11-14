"""Does storing the embedding model's weights as int8 change what search returns?

Two parts:

* The harness itself, run on synthetic vectors, so a broken measure or an
  unfrozen threshold is caught without a model. These always run.
* Runs on the real model through the production path (the real server app and
  client, in process). They are slow and need the model's weights, so they
  start only when ``EMBEDDING_EQUIVALENCE_RUN`` is set to ``pilot`` or ``full``;
  the full run also needs the thresholds in the pre-registration file frozen.
  The Qdrant comparison needs a Qdrant instance and is skipped without one.

Run:  EMBEDDING_EQUIVALENCE_RUN=pilot pytest tests/integration/test_embedding_precision_equivalence.py -m integration
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from tests.support import embedding_equivalence as eq

pytestmark = pytest.mark.integration

PREREGISTRATION = Path(__file__).with_name("embedding_precision_preregistration.json")
REPO_ROOT = Path(__file__).resolve().parents[4]
RUN = os.environ.get("EMBEDDING_EQUIVALENCE_RUN", "").strip().lower()


def _registration() -> dict:
    return json.loads(PREREGISTRATION.read_text())


class TestPreregistration:
    def test_seeds_are_distinct_so_the_pilot_subset_is_never_reused(self) -> None:
        seeds = _registration()["seeds"]
        assert len(set(seeds.values())) == len(seeds)
        pilot = {v for k, v in seeds.items() if k.startswith("pilot_")}
        full = {v for k, v in seeds.items() if not k.startswith("pilot_")}
        assert pilot and full and not pilot & full

    # Diagnostics may be fixed from the pilot while the gating thresholds are still open.
    _DIAGNOSTIC = ("cosine_mean_floor", "cosine_min_floor", "t1_diagnostic_mean")

    def test_gating_thresholds_are_all_unset_or_all_set(self) -> None:
        reg = _registration()
        values = [v for k, v in reg["thresholds"].items() if k not in self._DIAGNOSTIC]
        frozen = [v is not None for v in values]
        assert all(frozen) or not any(frozen)
        assert (reg["status"] == "frozen") == all(frozen)
        if reg["status"] == "frozen":
            assert all(reg["thresholds"][k] is not None for k in self._DIAGNOSTIC)

    def test_the_pilot_is_the_size_the_plan_names(self) -> None:
        pilot = _registration()["pilot"]
        assert sum(pilot[k] for k in ("prose_chunks", "number_heavy_chunks", "long_chunks", "near_duplicate_chunks")) == 500
        assert pilot["queries"] == 200

    def test_controls_are_registered(self) -> None:
        assert _registration()["controls"]["dynamic_activation_variant_must_fail_t4"] is True


class TestHarnessOnSyntheticVectors:
    """The proof's measures on vectors whose answer is known."""

    def _world(self):  # noqa: ANN202
        rng = np.random.default_rng(11)
        docs = eq.unit(rng.standard_normal((600, 64)))
        queries = eq.unit(docs[:100] + 0.7 * rng.standard_normal((100, 64)))
        return queries, docs

    def test_an_equivalent_encoder_passes_and_a_degraded_one_fails(self) -> None:
        queries, docs = self._world()
        ref = eq.rank_documents(queries, docs, 10)
        near = eq.rank_documents(eq.degrade(queries, 0.01, 1), eq.degrade(docs, 0.01, 2), 10)
        bad = eq.rank_documents(eq.degrade(queries, 1.5, 3), eq.degrade(docs, 1.5, 4), 10)
        assert eq.overlap_at_k(ref, near, 10).mean() >= 0.95
        assert eq.overlap_at_k(ref, bad, 10).mean() < 0.95
        assert eq.top1_agreement(ref, bad).mean() < 0.97

    def test_a_biased_encoder_moves_the_mixed_index_measure(self) -> None:
        queries, docs = self._world()
        neutral = eq.mixed_index_pairs(queries, docs, eq.degrade(docs, 0.01, 5), top=10)
        skewed = eq.mixed_index_pairs(eq.biased(queries, 0.5, 6), docs, eq.biased(docs, 0.5, 6), top=10)
        assert abs(neutral.new_outranks_old - 0.5) <= 0.05 and abs(neutral.gap_mean) <= 0.002
        assert abs(skewed.new_outranks_old - 0.5) > 0.05 or abs(skewed.gap_mean) > 0.002


needs_model = pytest.mark.skipif(RUN not in {"pilot", "full"}, reason="set EMBEDDING_EQUIVALENCE_RUN=pilot or full")


def _frozen_thresholds() -> dict:
    reg = _registration()
    if reg["status"] != "frozen":
        pytest.skip("thresholds are not frozen yet; the pilot proposes them")
    return reg["thresholds"]


async def _corpus(shape: dict, seeds: dict, prefix: str):  # noqa: ANN202
    prose = eq.markdown_chunks(REPO_ROOT, shape["prose_chunks"], seeds[f"{prefix}_corpus"])
    numbers = eq.number_heavy_chunks(shape["number_heavy_chunks"], seeds[f"{prefix}_number_heavy"])
    longs = eq.long_chunks(prose, shape["long_chunks"], seeds[f"{prefix}_long"], words=350)
    copies, _ = eq.near_duplicates(prose, shape["near_duplicate_chunks"], seeds[f"{prefix}_near_duplicates"])
    docs = prose + numbers + longs + copies
    queries, gold = eq.paraphrase_queries(prose, shape["queries"], seeds[f"{prefix}_queries" if f"{prefix}_queries" in seeds else f"{prefix}_prose_queries"])
    return docs, queries, gold


@needs_model
class TestRealWeights:
    async def test_production_path_reports_and_is_batch_invariant(self) -> None:
        from tests.support.embedding_equivalence_path import (
            ServerPath,
            batch_dependence,
            dynamic_activation_variant,
        )

        reg = _registration()
        model = reg["model"]
        async with ServerPath(model, "int8").running() as path:
            assert path.info["reported_precision"] == "int8-w"
            one, many = await batch_dependence(path)
            vector = (await path.queries(["unit norm check"]))[0]
        assert min(one, many) >= 0.99999
        assert abs(np.linalg.norm(vector) - 1) < 1e-3 and vector.shape == (1024,) and np.isfinite(vector).all()
        with dynamic_activation_variant():
            async with ServerPath(model, "int8").running() as control:
                c_one, c_many = await batch_dependence(control)
        assert min(c_one, c_many) < 0.99999, "the batch-dependence test cannot see a dynamic-activation encoder"

    async def test_pilot_or_full_retrieval(self) -> None:
        from tests.support.embedding_equivalence_report import run_report

        reg = _registration()
        thresholds = _frozen_thresholds() if RUN == "full" else None
        report = await run_report(reg, RUN, thresholds)
        if thresholds is not None:
            assert report["verdict"]["failed"] == [], report["verdict"]
