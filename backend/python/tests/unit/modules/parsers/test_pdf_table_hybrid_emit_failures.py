"""Any failure of the model path is the evidence result: for one page of five, that page's tables are the evidence ones and
the others carry model grids; for every page, the bytes equal ``evidence``. Without a configuration ``hybrid`` is
``evidence``. The worker never raises because of the mode."""
from __future__ import annotations

import logging
import pickle

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_hybrid_emit as emit
from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)
from tests.support.table_emit import (
    FakeModelClient,
    canonical,
    cfg,
    fake_boxes,
    fixture_pdf,
)

PDF, BOXES = fixture_pdf()


@pytest.fixture(autouse=True)
def boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes(BOXES))


def with_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    real = emit.TableEmitter.__init__
    monkeypatch.setattr(emit.TableEmitter, "__init__", lambda self, c, p, **kw: real(self, c, p, **dict(kw, client=client)))


def run(mode: str, monkeypatch: pytest.MonkeyPatch, client: object | None = None, config=None):  # noqa: ANN201
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", mode)
    if client is not None:
        with_client(monkeypatch, client)
    return parse_pdf_layout(PDF, None, config)


def model_tables(page) -> list:
    return [r for r in page.regions if r.type == LayoutRegionType.TABLE and r.from_model_grid]


class Seq(FakeModelClient):
    def __init__(self, seq: list[str]) -> None:
        super().__init__()
        self.seq = seq

    def post_batch(self, *a, **k):  # noqa: ANN201
        self.outcome = None if self.seq[self.calls] == "ok" else self.seq[self.calls]
        return super().post_batch(*a, **k)


@pytest.mark.parametrize("kind", ["timeout", "error", "service_busy", "service_down", "unsupported", "mode_fast"])
def test_one_failing_page_is_evidence_and_the_others_are_model(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    evidence = run("evidence", monkeypatch)
    pages = run("hybrid", monkeypatch, Seq(["ok", kind, "ok", "ok", "ok"]), cfg())
    latching = kind in ("service_busy", "service_down", "unsupported", "mode_fast")
    assert model_tables(pages[0]) and not model_tables(pages[1])
    assert canonical([pages[1]]) == canonical([evidence[1]])
    if latching:  # the rest of the document is evidence
        assert not any(model_tables(p) for p in pages[1:])
    else:  # page 4 is a rule_conflict on the fixture; pages 3 and 5 keep their model grids
        assert model_tables(pages[2]) and model_tables(pages[4])


@pytest.mark.parametrize("kind", ["timeout", "error", "service_busy", "service_down", "unsupported"])
def test_every_page_failing_gives_the_evidence_bytes(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    evidence = run("evidence", monkeypatch)
    hybrid = run("hybrid", monkeypatch, FakeModelClient(outcome=kind), cfg())
    assert canonical(hybrid) == canonical(evidence)
    assert pickle.dumps(hybrid, protocol=4) == pickle.dumps(evidence, protocol=4)


def test_hybrid_without_a_configuration_is_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pickle.dumps(run("hybrid", monkeypatch), protocol=4) == pickle.dumps(run("evidence", monkeypatch), protocol=4)


def test_a_configuration_in_another_mode_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeModelClient()
    assert canonical(run("evidence", monkeypatch, client, cfg())) == canonical(run("evidence", monkeypatch))
    assert client.calls == 0


def test_a_bug_in_the_swap_gives_the_evidence_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = run("evidence", monkeypatch)

    def boom(self, *a, **k):  # noqa: ANN202
        raise RuntimeError("bug")

    monkeypatch.setattr(emit.TableEmitter, "_swap_page", boom)
    assert canonical(run("hybrid", monkeypatch, FakeModelClient(), cfg())) == canonical(evidence)


def test_a_client_that_raises_is_an_error_not_a_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Raising(FakeModelClient):
        def post_batch(self, *a, **k):  # noqa: ANN202
            raise RuntimeError("boom")

    evidence = run("evidence", monkeypatch)
    assert canonical(run("hybrid", monkeypatch, Raising(), cfg())) == canonical(evidence)


def test_a_failing_render_is_the_evidence_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path):  # noqa: ANN202
        def render(i):  # noqa: ANN202
            raise RuntimeError("pdfium")

        return render

    monkeypatch.setattr(emit, "_default_renderer", broken)
    evidence = run("evidence", monkeypatch)
    assert canonical(run("hybrid", monkeypatch, FakeModelClient(), cfg())) == canonical(evidence)


def test_a_model_that_leaves_words_out_is_the_evidence_result_for_those_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = run("evidence", monkeypatch)
    pages = run("hybrid", monkeypatch, FakeModelClient({"p3:b0": "drop:0", "p3:b1": "drop:0"}), cfg())
    assert not model_tables(pages[2]) and canonical([pages[2]]) == canonical([evidence[2]])
    assert model_tables(pages[0])


def test_the_model_config_pickles_and_has_no_credentials() -> None:
    c = cfg()
    assert pickle.loads(pickle.dumps(c)) == c
    assert not {f for f in c.__dataclass_fields__ if any(x in f for x in ("token", "key", "secret", "password", "auth"))}


# --- through the processor ------------------------------------------------------------------------------------------------


async def test_parse_document_hands_the_worker_a_configuration_only_for_record_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "hybrid")
    seen: list[tuple] = []

    async def inline(fn, *args, **kw):  # noqa: ANN202
        seen.append(args)
        return fn(*args)

    monkeypatch.setattr(parse_pool, "submit", inline)
    with_client(monkeypatch, FakeModelClient())
    proc = PDFPlumberOpenCVProcessor(logging.getLogger("t"), None)  # type: ignore[arg-type]
    pages = await proc.parse_document("a.pdf", PDF, table_model=True, record_id="rec-9")
    assert isinstance(seen[0][2], emit.ModelCfg) and seen[0][2].record_id == "rec-9" and seen[0][2].max_tables == 300 and seen[0][2].doc_seconds == 600.0
    assert any(model_tables(p) for p in pages)
    seen.clear()
    await proc.parse_document("a.pdf", PDF)  # a chat attachment or other caller: no model call
    assert seen == [(PDF,)]


async def test_a_latched_service_means_no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.pdf import table_hybrid_flow as flow

    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "hybrid")
    coord = flow.ShadowCoordinator()
    coord.latch("service_down", 30.0)
    flow.set_coordinator(coord)
    try:
        proc = PDFPlumberOpenCVProcessor(logging.getLogger("t"), None)  # type: ignore[arg-type]
        assert proc._hybrid_cfg("r", "q", table_model=True) is None
    finally:
        flow.set_coordinator(None)
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "evidence")
    assert proc._hybrid_cfg("r", "q", table_model=True) is None
