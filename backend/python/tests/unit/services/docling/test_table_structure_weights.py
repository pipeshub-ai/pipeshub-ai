"""Where the service finds the structure model's weights: the artifacts folder when it holds them, else the pinned
revision from the hub cache, offline; and what a missing model does (no crash, the record is unaffected)."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest
from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled

from app.modules.parsers.pdf import table_hybrid as th
from app.modules.parsers.pdf import table_hybrid_flow as flow
from app.modules.parsers.pdf.table_hybrid_client import TableStructureClient
from app.services.docling import table_structure as ts
from app.services.docling import table_structure_schema as sch
from tests.support.table_shadow import parse_logs, spool_tables

if TYPE_CHECKING:
    from pathlib import Path


def put_files(base: Path, mode: str = "accurate") -> Path:
    folder = base / "model_artifacts" / "tableformer" / mode
    folder.mkdir(parents=True)
    (folder / "tm_config.json").write_text("{}")
    (folder / f"tableformer_{mode}.safetensors").write_bytes(b"w")
    return base / "model_artifacts" / "tableformer"


def hub_cache(home: Path, revision: str, mode: str = "accurate") -> Path:
    """A hub cache laid out as the hub library writes it, holding the files of one revision."""
    repo = home / "hub" / "models--docling-project--docling-models"
    snap = repo / "snapshots" / revision
    put_files(snap, mode)
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text(revision)
    return snap


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
    monkeypatch.delenv(ts.ARTIFACTS_PATH_ENV, raising=False)
    import huggingface_hub.constants as c

    monkeypatch.setattr(c, "HF_HUB_OFFLINE", True, raising=False)
    monkeypatch.setattr(c, "HF_HUB_CACHE", str(tmp_path / "hf-home" / "hub"), raising=False)
    monkeypatch.setattr(c, "HUGGINGFACE_HUB_CACHE", str(tmp_path / "hf-home" / "hub"), raising=False)


def test_the_revision_is_a_commit_and_the_two_sides_agree() -> None:
    assert len(sch.MODEL_REVISION) == 40 and all(ch in "0123456789abcdef" for ch in sch.MODEL_REVISION)
    assert th.MODEL_REVISION == sch.MODEL_REVISION
    assert ts.MODEL_REVISION == sch.MODEL_REVISION and ts.MODEL_REPO == "docling-project/docling-models"


def test_the_artifacts_folder_is_used_when_it_holds_the_files(tmp_path, monkeypatch) -> None:
    root = put_files(tmp_path / "artifacts")
    monkeypatch.setenv(ts.ARTIFACTS_PATH_ENV, str(tmp_path / "artifacts"))
    assert ts.resolve_weights_dir("accurate") == str(root)


def test_an_artifacts_folder_without_the_files_falls_back_to_the_pinned_revision_in_the_cache(tmp_path, monkeypatch) -> None:
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv(ts.ARTIFACTS_PATH_ENV, str(tmp_path / "empty"))
    snap = hub_cache(tmp_path / "hf-home", sch.MODEL_REVISION)
    got = ts.resolve_weights_dir("accurate")
    assert os.path.realpath(got) == os.path.realpath(str(snap / "model_artifacts" / "tableformer"))


def test_the_cache_is_read_at_the_pinned_revision_never_at_main(tmp_path) -> None:
    hub_cache(tmp_path / "hf-home", "0" * 40)  # some other revision, and refs/main pointing at it
    with pytest.raises((OfflineModeIsEnabled, LocalEntryNotFoundError)):
        ts.resolve_weights_dir("accurate")


def test_nothing_anywhere_raises_offline(tmp_path) -> None:
    with pytest.raises((OfflineModeIsEnabled, LocalEntryNotFoundError)):
        ts.resolve_weights_dir("accurate")


async def test_missing_weights_are_a_clean_error_not_a_crash(tmp_path) -> None:
    sup = ts.TableSupervisor(memory_available=lambda: 10**12, factory="tests.unit.services.docling.fake_table_model:build")
    result = await sup.run_table({"id": "a", "scale_factor": 1.0, "page_w": 1.0, "page_h": 1.0, "table_bbox": [0, 0, 1, 1], "cap": 10, "tokens": []}, b"x")
    assert result == {"status": "error", "err": "weights"}


async def test_a_service_without_the_model_is_recorded_unsupported_and_the_next_document_is_not_started(tmp_path, monkeypatch, caplog) -> None:
    import logging
    from unittest.mock import patch

    import app.services.docling.docling_service as mod
    from tests.support.table_shadow import CountingGate, FakeGovernor

    monkeypatch.setenv("PIPESHUB_TABLE_SPOOL_DIR", str(tmp_path / "spool"))
    caplog.set_level(logging.INFO, logger="app.modules.parsers.pdf.table_hybrid_flow")
    sup = ts.TableSupervisor(memory_available=lambda: 10**12, factory="tests.unit.services.docling.fake_table_model:build")

    async def reserve(permit, demand, *, deadline_s=None, cancel=None) -> None:
        return None

    transport = httpx.ASGITransport(app=mod.app)
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", transport))
    adm = coord.admit("rec", "req")
    spool_tables(adm.spool_dir, 3)
    with patch.object(mod, "_resource_governor", FakeGovernor(CountingGate(4))), patch.dict(ts._holder, {"s": sup}), patch.object(mod, "reserve_or_adopt", reserve):
        await coord.start(adm, "rec", "req")
    tables, docs = parse_logs(caplog)
    assert {t["reason"] for t in tables} == {"unsupported"} and docs[0]["outcome"] == "ok"
    assert coord.admit("rec2", "req2") is None
