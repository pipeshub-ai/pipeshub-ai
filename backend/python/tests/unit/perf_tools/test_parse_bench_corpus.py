"""The corpus is deterministic, bounded and covers every kind."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.perf.parse_bench import corpus

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def smoke(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, corpus.Manifest]:
    out = tmp_path_factory.mktemp("smoke")
    return out, corpus.generate(out, profile="smoke", seed=3)


def test_every_kind_is_present(smoke: tuple[Path, corpus.Manifest]) -> None:
    kinds = {e.kind for e in smoke[1].entries}
    assert {"digital", "scanned", "tables", "figures", "damaged", "docx", "md", "json", "txt"} <= kinds
    assert "large" not in kinds


def test_same_seed_same_bytes(smoke: tuple[Path, corpus.Manifest], tmp_path: Path) -> None:
    again = corpus.generate(tmp_path, profile="smoke", seed=3)
    assert [e.sha256 for e in again.entries] == [e.sha256 for e in smoke[1].entries]
    assert again.corpus_id == smoke[1].corpus_id


def test_other_seed_other_bytes(smoke: tuple[Path, corpus.Manifest], tmp_path: Path) -> None:
    other = corpus.generate(tmp_path, profile="smoke", seed=4)
    assert other.corpus_id != smoke[1].corpus_id


def test_manifest_round_trips(smoke: tuple[Path, corpus.Manifest]) -> None:
    loaded = corpus.load_manifest(smoke[0])
    assert loaded.corpus_id == smoke[1].corpus_id
    assert loaded.entries[0].path == smoke[1].entries[0].path


def test_files_match_the_manifest(smoke: tuple[Path, corpus.Manifest]) -> None:
    for entry in smoke[1].entries:
        assert (smoke[0] / entry.path).stat().st_size == entry.bytes


def test_scanned_pdf_has_no_text_layer(smoke: tuple[Path, corpus.Manifest]) -> None:
    import io

    import pdfplumber

    entry = next(e for e in smoke[1].entries if e.kind == "scanned")
    with pdfplumber.open(io.BytesIO((smoke[0] / entry.path).read_bytes())) as pdf:
        assert all(not (page.extract_text() or "").strip() for page in pdf.pages)
        assert all(page.images for page in pdf.pages)


def test_digital_pdf_has_text(smoke: tuple[Path, corpus.Manifest]) -> None:
    import io

    import pdfplumber

    entry = next(e for e in smoke[1].entries if e.kind == "digital")
    with pdfplumber.open(io.BytesIO((smoke[0] / entry.path).read_bytes())) as pdf:
        assert len(pdf.pages[0].extract_text() or "") > 200


def test_damaged_variants_differ_from_a_good_file() -> None:
    words = corpus.Words(__import__("random").Random(1))
    good = corpus.digital_pdf(words, 2)
    for variant in range(3):
        broken = corpus.damaged_pdf(corpus.Words(__import__("random").Random(1)), 2, variant)
        assert broken != good


def test_docx_bytes_do_not_depend_on_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    import random
    import time

    first = corpus.docx_file(corpus.Words(random.Random(2)), 1)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 86400 * 400)
    monkeypatch.setattr(time, "localtime", lambda *a: time.gmtime(real() + 86400 * 400))
    assert corpus.docx_file(corpus.Words(random.Random(2)), 1) == first


class TestSizeBudget:
    def test_refuses_a_corpus_over_the_budget(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="over --max-mb"):
            corpus.generate(tmp_path, profile="standard", max_mb=50)

    def test_nothing_is_written_when_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            corpus.generate(tmp_path / "c", profile="standard", max_mb=50)
        assert not (tmp_path / "c").exists()

    def test_large_mb_zero_drops_the_big_file(self) -> None:
        params = corpus.resolve_params("standard", 1.0, 0, None)
        assert params["large_mb"] == 0
        assert corpus.estimate_bytes(params) < 200 * corpus.MIB

    def test_standard_estimate_is_a_few_hundred_mb(self) -> None:
        assert 100 * corpus.MIB < corpus.estimate_bytes(corpus.resolve_params("standard", 1.0, None, None)) < 1024 * corpus.MIB

    def test_scale_changes_counts_not_pages(self) -> None:
        params = corpus.resolve_params("standard", 2.0, None, None)
        assert params["digital"] == (12, 10)

    def test_unknown_profile(self) -> None:
        with pytest.raises(ValueError, match="unknown profile"):
            corpus.resolve_params("nope", 1.0, None, None)


def test_large_pdf_reaches_the_target_size() -> None:
    data = corpus.large_pdf(1, 20)
    assert 18 * corpus.MIB < len(data) < 60 * corpus.MIB
