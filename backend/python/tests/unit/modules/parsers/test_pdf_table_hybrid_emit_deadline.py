"""The emit phase's bars and latches: model-time bar, record cutoff, one request per page, 429 and connect latches, the
three-page latch, and nothing reported to the governor."""
from __future__ import annotations

import pytest

from app.modules.parsers.pdf import table_hybrid_emit as emit
from app.modules.parsers.pdf.table_hybrid_emit import TableEmitter
from tests.support.table_emit import (
    FakeModelClient,
    cfg,
    reasons,
    run_page,
)
from tests.support.table_shadow import blank_renderer, grid_words


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def page_boxes(n: int):  # noqa: ANN201
    boxes = [(40.0, 20.0 + 110 * i, 300.0, 20.0 + 110 * i + 100) for i in range(n)]
    return boxes, [w for b in boxes for w in grid_words(b)]


def emitter(client, clock: Clock | None = None, **kw):  # noqa: ANN201
    return TableEmitter(cfg(**kw), "x.pdf", client=client, renderer=blank_renderer, clock=clock or Clock())


def run_pages(em: TableEmitter, client: FakeModelClient, pages: int, per_page: int = 1) -> None:
    boxes, words = page_boxes(per_page)
    em.begin(pages)
    for p in range(pages):
        run_page(em, words, boxes, page_index=p, n_pages=pages)
        em.note_page(0.01)


def test_bar_check_is_pure() -> None:
    kw = {"est_s": 10.0, "doc_s": 600.0, "now": 100.0, "cutoff_epoch": 1000.0, "pages_left": 10, "avg_plain_page_s": 1.0}
    assert emit.bar_check(model_used_s=0.0, **kw) is None
    assert emit.bar_check(model_used_s=591.0, **kw) == "deadline"  # model time
    assert emit.bar_check(model_used_s=590.0, **kw) is None  # exactly at the bar is allowed
    assert emit.bar_check(model_used_s=0.0, **dict(kw, now=981.0)) == "deadline"  # record cutoff: 981 + 10 + 10 > 1000
    assert emit.bar_check(model_used_s=0.0, **dict(kw, now=980.0)) is None


def test_the_page_estimate_is_the_supervisors_per_step_bound() -> None:
    assert emit.page_estimate_s([100, 50]) == pytest.approx(9.0)


def test_one_request_per_page_whatever_the_number_of_boxes() -> None:
    client = FakeModelClient()
    em = emitter(client)
    run_pages(em, client, pages=3, per_page=3)
    assert client.calls == 3
    assert [len(r["meta"]["tables"]) for r in client.requests] == [3, 3, 3]


def test_a_page_with_more_than_sixteen_boxes_is_split() -> None:
    client = FakeModelClient()
    boxes = [(40.0, 5.0 + 36 * i, 300.0, 5.0 + 36 * i + 30) for i in range(20)]
    words = [
        {"text": f"w{i}{r}{c}", "x0": b[0] + 4 + 60 * c, "x1": b[0] + 24 + 60 * c, "top": b[1] + 2 + 13 * r, "bottom": b[1] + 12 + 13 * r}
        for i, b in enumerate(boxes) for r in range(2) for c in range(2)
    ]
    run_page(emitter(client), words, boxes)
    assert [len(r["meta"]["tables"]) for r in client.requests] == [16, 4]


def test_the_model_time_bar_stops_this_page_and_every_later_one() -> None:
    client = FakeModelClient()
    em = emitter(client, doc_seconds=3.0)  # a page's estimate is 0.06 * cap, about 3.4 s for the fixture box
    run_pages(em, client, pages=3)
    assert client.calls == 0 and reasons(em) == ["deadline"] * 3 and em.totals.deadline_hit_page == 1
    em2 = emitter(FakeModelClient(), doc_seconds=600.0)
    em2.totals.model_used_s = 599.0
    run_pages(em2, FakeModelClient(), pages=2)
    assert reasons(em2) == ["deadline", "deadline"] and em2.totals.deadline_hit_page == 1


def test_the_record_cutoff_after_four_thousand_seconds_makes_no_call() -> None:
    start = 1_000_000.0
    record_timeout = 5400.0
    config = emit.make_cfg("r", "q", "http://svc", record_timeout_s=record_timeout, now=start)
    assert config.cutoff_epoch == pytest.approx(start + 0.8 * record_timeout - 600.0)
    late, early = FakeModelClient(), FakeModelClient()
    em = TableEmitter(config, "x.pdf", client=late, renderer=blank_renderer, clock=Clock(start + 4000.0))
    run_pages(em, late, pages=2)
    assert late.calls == 0 and reasons(em) == ["deadline", "deadline"] and em.totals.cutoff_hit_page == 1
    em = TableEmitter(config, "x.pdf", client=early, renderer=blank_renderer, clock=Clock(start))
    run_pages(em, early, pages=2)
    assert early.calls == 2


def test_the_cutoff_counts_the_pages_still_to_come() -> None:
    client = FakeModelClient()
    clock = Clock(1_000.0)
    em = TableEmitter(cfg(cutoff_epoch=1_000.0 + 100.0), "x.pdf", client=client, renderer=blank_renderer, clock=clock)
    boxes, words = page_boxes(1)
    em.begin(50)
    em.note_page(2.0)  # the mean page takes 2 s without the model: 49 more pages need 98 s, the estimate 3.4 s
    run_page(em, words, boxes, page_index=0, n_pages=50)
    assert client.calls == 0 and reasons(em) == ["deadline"]


@pytest.mark.parametrize(("kind", "later"), [("service_busy", "service_busy"), ("service_down", "service_down"), ("unsupported", "unsupported"), ("deadline", "deadline")])
def test_a_429_or_a_connect_error_latches_the_rest_of_the_document(kind: str, later: str) -> None:
    client = FakeModelClient(outcome=kind)
    em = emitter(client)
    run_pages(em, client, pages=4)
    assert client.calls == 1 and reasons(em) == [kind] * 4  # one attempt, no retry, no loop


class Seq(FakeModelClient):
    """Answers request n with ``seq[n]``: an outcome kind, or ``ok``."""

    def __init__(self, seq: list[str]) -> None:
        super().__init__()
        self.seq = seq

    def post_batch(self, *a, **k):  # noqa: ANN201
        self.outcome = None if self.seq[self.calls] == "ok" else self.seq[self.calls]
        return super().post_batch(*a, **k)


def test_a_422_fails_that_pages_tables_only() -> None:
    client = Seq(["invalid_input", "ok"])
    em = emitter(client)
    run_pages(em, client, pages=2)
    assert reasons(em) == ["invalid_input", "none"]


def test_a_timeout_fails_that_pages_tables_only() -> None:
    client = Seq(["timeout", "ok", "ok"])
    em = emitter(client)
    run_pages(em, client, pages=3)
    assert reasons(em) == ["timeout", "none", "none"]


def test_three_failing_pages_in_a_row_latch_service_down() -> None:
    client = FakeModelClient(outcome="error")
    em = emitter(client)
    run_pages(em, client, pages=5)
    assert client.calls == 3 and reasons(em) == ["error", "error", "error", "service_down", "service_down"]


def test_a_good_page_resets_the_failure_count() -> None:
    client = Seq(["error", "error", "ok", "error", "error", "ok"])
    em = emitter(client)
    run_pages(em, client, pages=6)
    assert client.calls == 6 and reasons(em)[2] == "none" and reasons(em)[5] == "none"


def test_the_request_carries_a_deadline_and_a_timeout_inside_the_bar() -> None:
    client = FakeModelClient()
    em = emitter(client, doc_seconds=100.0)
    run_pages(em, client, pages=1)
    req = client.requests[0]
    assert 0 < req["timeout_s"] <= 100.0 and 0 < req["deadline_ms"] <= 100_000


def test_the_sync_client_never_touches_the_downstream_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.modules.parsers.pdf.table_hybrid_sync_client import SyncTableClient
    from app.services import resource_governor

    def boom(*a, **k):  # noqa: ANN202
        raise AssertionError("downstream feedback")

    monkeypatch.setattr(resource_governor, "get_default_downstream_feedback", boom)
    for code in (429, 500, 404):
        c = SyncTableClient("http://svc", httpx.MockTransport(lambda r, code=code: httpx.Response(code, json={})))
        assert c.post_batch({"v": 1, "tables": []}, {"c0": b"x"}, deadline_ms=1000, timeout_s=1.0).kind != "ok"

    def refuse(request):  # noqa: ANN202
        raise httpx.ConnectError("refused", request=request)

    assert SyncTableClient("http://svc", httpx.MockTransport(refuse)).post_batch({}, {}, deadline_ms=1, timeout_s=1.0).kind == "service_down"


def test_the_sync_client_sends_the_deadline_header_and_the_parts() -> None:
    import httpx

    from app.modules.parsers.pdf.table_hybrid_sync_client import SyncTableClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, json={})

    out = SyncTableClient("http://svc", httpx.MockTransport(handler)).post_batch({"v": 1, "tables": []}, {"c0": b"png"}, deadline_ms=5000, timeout_s=5.0)
    assert out.kind == "service_busy" and seen[0].headers["X-Table-Deadline-Ms"] == "5000" and seen[0].url.path == "/table-structure"


def test_an_error_text_never_carries_the_service_url(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    cf = cfg(base_url="http://user:secret@svc.internal:8081")
    em = TableEmitter(cf, "x.pdf", client=FakeModelClient(outcome="error"), renderer=blank_renderer)
    boxes, words = page_boxes(1)
    run_page(em, words, boxes)
    em.finish()
    assert "secret" not in caplog.text and "svc.internal" not in caplog.text


def test_the_sync_client_ends_a_trickled_answer_at_its_timeout() -> None:
    import time

    import httpx

    from app.modules.parsers.pdf.table_hybrid_sync_client import SyncTableClient

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):  # noqa: ANN204
            for _ in range(30):  # each chunk well inside any per-read timeout, the whole answer far past the budget
                time.sleep(0.1)
                yield b" "

    c = SyncTableClient("http://svc", httpx.MockTransport(lambda r: httpx.Response(200, stream=Trickle())))
    t0 = time.monotonic()
    out = c.post_batch({"v": 1, "tables": []}, {"c0": b"x"}, deadline_ms=1000, timeout_s=0.5)
    assert out.kind == "timeout" and time.monotonic() - t0 < 1.5
