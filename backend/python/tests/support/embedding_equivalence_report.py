"""Runs the precision comparison on the real model and reports every measure.

Dense-only retrieval here is an exact cosine search in memory. The Qdrant
variants (hybrid fusion, collection quantisation on and off) are in
``qdrant_hybrid_rankings`` and need a running Qdrant.


The full run pins its corpus by hash (``corpus_sha256`` in the pre-registration). The corpus is drawn from the
repository's Markdown, so editing any ``.md`` file changes the chunk pool and a later run needs the hash re-pinned.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tests.support import embedding_equivalence as eq
from tests.support.embedding_equivalence_path import encode_everything

if TYPE_CHECKING:
    from collections.abc import Sequence

TOP_DRIFT = 50
TOP = 10


def _corpus(reg: dict, mode: str):  # noqa: ANN202
    from tests.integration.test_embedding_precision_equivalence import REPO_ROOT

    prefix = "pilot" if mode == "pilot" else "full"
    shape, seeds = reg[mode], reg["seeds"]
    prose = eq.markdown_chunks(REPO_ROOT, shape["prose_chunks"], seeds[f"{prefix}_corpus"])
    numbers = eq.number_heavy_chunks(shape["number_heavy_chunks"], seeds[f"{prefix}_number_heavy"])
    longs = eq.long_chunks(prose, shape["long_chunks"], seeds[f"{prefix}_long"], words=350)
    copies, copy_of = eq.near_duplicates(prose, shape["near_duplicate_chunks"], seeds[f"{prefix}_near_duplicates"])
    query_seed = seeds["pilot_prose_queries"] if mode == "pilot" else seeds["full_queries"]
    queries, gold = eq.paraphrase_queries(prose, shape["queries"], query_seed)
    return prose + numbers + longs + copies, queries, gold, copy_of


def corpus_sha256(docs: Sequence[str], queries: Sequence[str]) -> str:
    h = hashlib.sha256()
    for part in (*docs, "\x00queries\x00", *queries):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()


def _pairing(name: str, q_ref, d_ref, q_new, d_new, hard_sets, gold, seed: int) -> dict:
    ref = eq.rank_documents(q_ref, d_ref, TOP)
    new = eq.rank_documents(q_new, d_new, TOP)
    overlap = eq.overlap_at_k(ref, new, TOP)
    top1 = eq.top1_agreement(ref, new)
    hit10_ref, hit10_new = eq.hit_at_k(ref, hard_sets, 10), eq.hit_at_k(new, hard_sets, 10)
    hit1_ref, hit1_new = eq.hit_at_k(ref, hard_sets, 1), eq.hit_at_k(new, hard_sets, 1)
    drop10 = eq.paired_bootstrap_ci(hit10_ref, hit10_new, seed=seed)
    drop1 = eq.paired_bootstrap_ci(hit1_ref, hit1_new, seed=seed)
    return {
        "pairing": name,
        "overlap_at_10": dict(zip(("mean", "ci_lo", "ci_hi"), eq.paired_bootstrap_ci(overlap, seed=seed), strict=True)),
        "top1_identical": dict(zip(("mean", "ci_lo", "ci_hi"), eq.paired_bootstrap_ci(top1, seed=seed), strict=True)),
        "hit10": {"reference": float(hit10_ref.mean()), "new": float(hit10_new.mean())},
        "hit1": {"reference": float(hit1_ref.mean()), "new": float(hit1_new.mean())},
        "hit10_drop": dict(zip(("mean", "ci_lo", "ci_hi"), drop10, strict=True)),
        "hit1_drop": dict(zip(("mean", "ci_lo", "ci_hi"), drop1, strict=True)),
    }




async def run_report(reg: dict, mode: str, thresholds: dict | None) -> dict:
    model = reg["model"]
    docs, queries, gold, copy_of = _corpus(reg, mode)
    pinned = reg["corpus"].get("corpus_sha256")
    if mode == "full" and pinned:
        actual = corpus_sha256(docs, queries)
        if actual != pinned:
            raise RuntimeError(f"the corpus changed since it was pinned: {actual} != {pinned}")
    started = time.time()
    def log(msg):  # noqa: ANN202
        return print(f"[{time.time() - started:7.1f}s] {msg}", flush=True)

    fp32, info32 = await encode_everything(model, "fp32", docs, queries, progress=log)
    log("fp32 done")
    int8, info8 = await encode_everything(model, "int8", docs, queries, progress=log)
    log("int8 done")
    report: dict = {"mode": mode, "model": model, "docs": len(docs), "queries": len(queries), "server": {"fp32": info32, "int8": info8}}

    # Calibration scale: how far apart two runs of the *same* precision already are.
    # It is a property of the model and the host, measured by the pilot; the full run skips the
    # two extra passes because each costs as much as a comparison pass.
    calibrate = mode == "pilot"
    again = half = None
    if calibrate:
        again, _ = await encode_everything(model, "fp32", docs[::-1], queries[::-1], progress=log)
        again = {"docs": again["docs"][::-1], "queries": again["queries"][::-1]}
        log("fp32 rerun (reversed order) done")
        half, _ = await encode_everything(model, "fp32", docs, queries, bfloat16=True, progress=log)
        log("bf16 done")

    report["cosine"] = {
        "int8_vs_fp32_docs": eq.cosine_stats(fp32["docs"], int8["docs"]),
        "int8_vs_fp32_queries": eq.cosine_stats(fp32["queries"], int8["queries"]),
    }
    if calibrate:
        report["cosine"] |= {
            "fp32_rerun_docs": eq.cosine_stats(fp32["docs"], again["docs"]),
            "fp32_rerun_queries": eq.cosine_stats(fp32["queries"], again["queries"]),
            "bf16_vs_fp32_docs": eq.cosine_stats(fp32["docs"], half["docs"]),
            "bf16_vs_fp32_queries": eq.cosine_stats(fp32["queries"], half["queries"]),
        }
    norms = np.linalg.norm(np.vstack([int8["docs"], int8["queries"]]), axis=1)
    report["t5"] = {"dim": int(int8["docs"].shape[1]), "max_norm_error": float(np.abs(norms - 1).max()), "finite": bool(np.isfinite(int8["docs"]).all())}

    seed = reg["seeds"]["bootstrap"]
    hard_sets = eq.hard_negative_sets(fp32["docs"], gold, reg["corpus"]["hard_negative_cosine"])
    rivals = eq.nearest_rival(fp32["docs"], gold)
    report["hard_negatives"] = {"mean_set_size": float(np.mean([len(s) for s in hard_sets]))}
    q32, d32, q8, d8 = fp32["queries"], fp32["docs"], int8["queries"], int8["docs"]

    report["t2_score_drift_int8"] = eq.score_drift(q32, q8, d32, d8, TOP_DRIFT)
    if calibrate:
        report["t2_score_drift_fp32_rerun"] = eq.score_drift(q32, again["queries"], d32, again["docs"], TOP_DRIFT)
        report["t2_score_drift_bf16"] = eq.score_drift(q32, half["queries"], d32, half["docs"], TOP_DRIFT)

    report["pairings"] = [
        _pairing("P1 fp32 docs, int8 queries", q32, d32, q8, d32, hard_sets, gold, seed),
        _pairing("P2 int8 docs, int8 queries", q32, d32, q8, d8, hard_sets, gold, seed),
        _pairing("P3 int8 docs, fp32 queries", q32, d32, q32, d8, hard_sets, gold, seed),
    ]
    if calibrate:
        report["pairings"] += [
            _pairing("calibration: fp32 rerun", q32, d32, again["queries"], again["docs"], hard_sets, gold, seed),
            _pairing("calibration: bf16", q32, d32, half["queries"], half["docs"], hard_sets, gold, seed),
        ]
    report["gold_vs_rival_flip_rate"] = {"P2": _gold_flip_rate(q32, d32, q8, d8, gold, rivals)}
    if calibrate:
        report["gold_vs_rival_flip_rate"]["bf16"] = _gold_flip_rate(q32, d32, half["queries"], half["docs"], gold, rivals)

    mask = eq.random_split(len(docs), reg["seeds"]["full_split"])
    mixed_docs = eq.split_index(d32, d8, mask)
    mixed = eq.mixed_index_pairs(q8, d32, d8, TOP)
    report["tm_two_copies"] = mixed.__dict__
    report["tm_split_5050"] = _pairing("TM chunk-wise 50/50 split, int8 queries", q32, d32, q8, mixed_docs, hard_sets, gold, seed)
    log("metrics done")

    if thresholds is not None:
        report["verdict"] = judge(report, thresholds)
    out = os.environ.get("EMBEDDING_EQUIVALENCE_OUT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2, default=float))
    print(json.dumps(report, indent=2, default=float))
    return report


def _gold_flip_rate(q_ref, d_ref, q_new, d_new, gold, rivals) -> float:
    # The report's queries are generated per gold chunk in order, so query i answers gold[i].
    qr, qn = eq.unit(q_ref), eq.unit(q_new)
    dr, dn = eq.unit(d_ref), eq.unit(d_new)
    idx = np.arange(len(gold))
    ref_gold, ref_rival = np.sum(qr[idx] * dr[list(gold)], 1), np.sum(qr[idx] * dr[rivals], 1)
    new_gold, new_rival = np.sum(qn[idx] * dn[list(gold)], 1), np.sum(qn[idx] * dn[rivals], 1)
    return eq.rank_flip_rate(new_gold, new_rival, ref_gold, ref_rival)


def judge(report: dict, t: dict) -> dict:
    """Compare a report with frozen thresholds; every failed check is named."""
    failed: list[str] = []

    def need(ok: bool, what: str) -> None:
        if not ok:
            failed.append(what)

    cos = report["cosine"]["int8_vs_fp32_docs"]
    need(cos["mean"] >= t["cosine_mean_floor"], "cosine mean floor")
    need(cos["min"] >= t["cosine_min_floor"], "cosine min floor")
    need(cos["mean"] >= t["t1_diagnostic_mean"], "T1 diagnostic mean")
    drift = report["t2_score_drift_int8"]
    need(drift["p99"] <= t["t2_score_drift_p99"], "T2 p99 drift")
    need(drift["max"] <= t["t2_score_drift_max"], "T2 max drift")
    need(drift["p99"] <= drift["median_adjacent_gap_top10"] / 2, "T2 p99 within half the adjacent-rank gap")
    for p in report["pairings"][:3]:
        floor = t["t3_overlap_mean_p1"] if p["pairing"].startswith("P1") else t["t3_overlap_mean"]
        name = p["pairing"].split()[0]
        need(p["overlap_at_10"]["ci_lo"] >= floor, f"T3 {name} overlap CI lower bound")
        need(p["top1_identical"]["ci_lo"] >= t["t3_top1_identical"], f"T3 {name} top-1 CI lower bound")
        need(p["hit10_drop"]["ci_hi"] <= t["t3_hit10_drop"], f"T3 {name} hit@10 drop")
        need(p["hit1_drop"]["ci_hi"] <= t["t3_hit1_drop"], f"T3 {name} hit@1 drop")
    mixed = report["tm_two_copies"]
    need(abs(mixed["gap_mean"]) <= t["tm_abs_mean_gap"], "TM mean gap")
    need(mixed["gap_p99"] <= t["tm_p99_gap"], "TM p99 gap")
    need(abs(mixed["new_outranks_old"] - 0.5) <= t["tm_new_outranks_old_band"], "TM new-outranks-old rate")
    split = report["tm_split_5050"]
    need(split["overlap_at_10"]["ci_lo"] >= t["t3_overlap_mean"], "TM split overlap")
    need(report["t5"]["dim"] == 1024 and report["t5"]["max_norm_error"] <= t["t5_unit_norm_tolerance"] and report["t5"]["finite"], "T5 shape and norm")
    return {"failed": failed, "passed": not failed}


async def qdrant_hybrid_rankings(
    service, texts: Sequence[str], doc_vectors: np.ndarray, query_texts: Sequence[str], query_vectors: np.ndarray, *, quantization_on: bool,
) -> np.ndarray:
    """Top-10 fused (dense + BM25) results from a real Qdrant collection built with the production config.

    Needs a running Qdrant; not run in this environment.
    """
    from app.services.vector_db.models import (
        CollectionConfig,
        HybridSearchRequest,
        QuantizationType,
        VectorPoint,
    )
    from app.services.vector_db.sparse_embeddings import SparseEmbedder

    name = f"equivalence_{uuid.uuid4().hex[:8]}"
    config = CollectionConfig(
        embedding_size=int(doc_vectors.shape[1]),
        quantization=QuantizationType.SCALAR if quantization_on else QuantizationType.NONE,
    )
    sparse = SparseEmbedder()
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}/{i}")) for i in range(len(texts))]
    await service.create_collection(name, config)
    try:
        sparse_docs = await sparse.embed_documents(list(texts))
        points = [
            VectorPoint(id=ids[i], dense_vector=doc_vectors[i].tolist(), sparse_vector=sparse_docs[i],
                        payload={"page_content": texts[i], "metadata": {"orgId": "equivalence", "idx": i}})
            for i in range(len(texts))
        ]
        for start in range(0, len(points), 200):
            await service.upsert_points(name, points[start:start + 200])
        await service.flush_writes(name)
        await service.make_searchable(name)
        position = {pid: i for i, pid in enumerate(ids)}
        ranked = []
        for text, vector in zip(query_texts, query_vectors, strict=True):
            request = HybridSearchRequest(
                dense_query=vector.tolist(), sparse_query=await sparse.embed_query(text), text_query=text, limit=TOP,
            )
            (hits,) = await service.query_nearest_points(name, [request])
            ranked.append([position[h.id] for h in hits])
        return np.array([row + [-1] * (TOP - len(row)) for row in ranked])
    finally:
        await service.delete_collection(name)
