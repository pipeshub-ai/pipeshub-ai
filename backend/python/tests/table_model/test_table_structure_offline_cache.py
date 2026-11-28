"""The child starts and predicts from the hub cache with the hub offline, the weights found at the pinned revision."""
from __future__ import annotations

import os
from pathlib import Path

from app.services.docling import table_structure as ts
from app.services.docling.table_structure_schema import MODEL_REVISION
from tests.table_model import support


def test_predicts_from_a_cache_holding_the_pinned_revision_with_the_hub_offline(weights, tmp_path, monkeypatch) -> None:
    snap = tmp_path / "hf" / "hub" / "models--docling-project--docling-models" / "snapshots" / MODEL_REVISION / "model_artifacts" / "tableformer" / "accurate"
    snap.mkdir(parents=True)
    for name in os.listdir(Path(weights) / "accurate"):
        os.symlink(Path(weights) / "accurate" / name, snap / name)
    monkeypatch.delenv(ts.ARTIFACTS_PATH_ENV, raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    import huggingface_hub.constants as c

    monkeypatch.setattr(c, "HF_HUB_CACHE", str(tmp_path / "hf" / "hub"), raising=False)
    monkeypatch.setattr(c, "HF_HUB_OFFLINE", True, raising=False)
    sup = ts.TableSupervisor(memory_available=lambda: 10**12)
    try:
        page_no, box, words, plan = next((p["page"], b, w, pl) for p in support.fixture_pages() for b in p["boxes"] for w in [support.box_words(p["words"], b)] for pl in [support.plan(w)] if pl.reason == "none")
        spec, png = support.product_request(str(support.FIXTURES / "tables.pdf"), page_no, words, box, plan.cap)
        result = support.run_async(sup.run_table({k: v for k, v in spec.items() if k != "image"}, png))
        assert result["status"] == "ok"
        assert sup.model_info is not None and sup.model_info.model_revision == MODEL_REVISION
    finally:
        support.run_async(sup.shutdown())
