"""The ledger in the push service: who is refused, when, and what each refusal costs."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from app.connectors.sources.localKB.push import ledger as L
from app.connectors.sources.localKB.push.contract import PushRefusal

from .image_harness import ORG, USER, image_body, make_harness, ref

ME = (ORG, USER)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .image_harness import Harness


@pytest.fixture
async def h(tmp_path) -> AsyncIterator[Harness]:
    ledger = L.PushLedger()
    harness = await make_harness(tmp_path, ledger=ledger)
    harness.ledger = ledger
    spool_writes = []
    real_write = harness.svc.spool.write
    harness.svc.spool.write = lambda data: spool_writes.append(1) or real_write(data)
    harness.spool_writes = spool_writes
    yield harness
    await harness.store.stop()


async def refused(h, body: bytes) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        await h.push(body)
    return e.value


def nothing_after_the_parse(h) -> None:
    assert (
        not h.docs.info_calls
        and not h.graph.access_calls
        and h.mat.runs == 0
        and h.admission._taken == 0
    )


async def test_a_suspended_caller_is_refused_with_no_read_parse_or_spool_write(
    h,
) -> None:
    for i in range(3):
        h.ledger.strike(ME, f"b:{i}", L.TEXT)
    r = await refused(h, image_body([]))
    assert (r.status, r.reason) == (429, "PUSH_SUSPENDED")
    assert h.svc.worker.validate.await_count == 0 and not h.spool_writes
    assert (await h.push(image_body([]), user_id="someone-else")).status == "created"


async def test_the_same_body_after_two_strikes_is_refused_without_a_parse_and_a_trailing_byte_is_not(
    h,
) -> None:
    body = image_body([], text="crash")
    key = "b:" + hashlib.sha256(body).hexdigest()
    h.ledger.strike(ME, key, L.TEXT)
    h.ledger.strike(ME, key, L.TEXT)
    r = await refused(h, body)
    assert (r.status, r.extra["limit"]) == (413, "complexity")
    assert h.svc.worker.validate.await_count == 0 and not h.spool_writes
    assert (
        (await h.push(image_body([], text="crash "))).status == "created"
    )  # a variant is a new key: only the distinct-key rule bounds it


async def test_an_image_suspended_caller_still_pushes_text_and_costs_one_parse_and_nothing_after(
    h,
) -> None:
    h.docs.put(1)
    for i in range(4):
        h.ledger.strike(ME, f"r:{i}", L.IMAGE)
    r = await refused(h, image_body([ref(1)]))
    assert (r.status, r.reason) == (429, "PUSH_SUSPENDED") and "mage" in r.message
    assert (
        h.svc.worker.validate.await_count == 1
    )  # the references were only found by the parse
    nothing_after_the_parse(h)
    assert (await h.push(image_body([], text="only text"))).status == "created"


async def test_a_request_key_refused_after_two_strikes_costs_one_parse_and_nothing_after(
    h,
) -> None:
    h.docs.put(1)
    body = image_body([ref(1)])
    h.mat.script = [
        (["strike"], PushRefusal(503, "PUSH_TIMEOUT", "x")),
        (["strike"], PushRefusal(503, "PUSH_TIMEOUT", "x")),
    ]
    for _ in range(2):
        await refused(h, body)
    runs, infos = h.mat.runs, len(h.docs.info_calls)
    r = await refused(h, body)
    assert (r.status, r.reason) == (503, "PUSH_TIMEOUT")
    assert (
        h.mat.runs == runs
        and len(h.docs.info_calls) == infos
        and h.admission._taken == 0
    )


async def test_untrusted_retries_share_a_key_and_the_pair_is_refused_after_two(
    h,
) -> None:
    """The stored digest differs on every try (a fresh nonce); the strike key must not."""
    h.docs.put(1, trusted=False)
    body = image_body([ref(1)])
    h.mat.script = [
        (["strike"], PushRefusal(503, "PUSH_TIMEOUT", "x")),
        (["strike"], PushRefusal(503, "PUSH_TIMEOUT", "x")),
    ]
    for _ in range(2):
        await refused(h, body)
    third_runs = h.mat.runs
    await refused(h, body)
    assert h.mat.runs == third_runs, (
        "the third try never reached a materialiser: same user, same request key"
    )
    assert (
        len(h.ledger._rows[ME].strikes) == 2
        and len({k for _, k, _s in h.ledger._rows[ME].strikes}) == 1
    )


async def test_four_different_crashing_requests_suspend_images_and_not_text(h) -> None:
    h.docs.put(1)
    for i in range(4):
        h.mat.script = [(["strike"], PushRefusal(503, "PUSH_TIMEOUT", "x"))]
        await refused(h, image_body([ref(1)], text=f"variant {i}"))
    r = await refused(h, image_body([ref(1)], text="fifth"))
    assert (r.status, r.reason) == (429, "PUSH_SUSPENDED")
    assert (await h.push(image_body([], text="text still works"))).status == "created"


async def test_a_storage_brownout_never_suspends_an_honest_caller(h) -> None:
    h.docs.put(1)
    for i in range(
        4
    ):  # four slow pushes in a row, each a different request, none CPU-bound
        h.mat.script = [(["overrun"], PushRefusal(503, "PUSH_TIMEOUT", "x"))]
        await refused(h, image_body([ref(1)], text=f"slow {i}"))
    h.ledger.check_all(ME)
    h.ledger.check_images(ME)
    assert not h.ledger._rows[ME].strikes
    assert (
        await h.push(image_body([ref(1)], text="after the brownout"))
    ).status == "created"


async def test_the_workers_seconds_budget_refuses_before_a_lease_is_taken(h) -> None:
    h.ledger.lease(ME, 151.0)
    r = await refused(h, image_body([]))
    assert (r.status, r.reason) == (503, "PUSH_TIMEOUT") and r.extra["retryAfter"] > 0
    assert h.svc.worker.validate.await_count == 0 and not h.spool_writes
    assert (await h.push(image_body([]), user_id="someone-else")).status == "created"


async def test_the_service_hands_the_worker_and_the_materialiser_an_accountant_only_when_it_has_a_ledger(
    h, tmp_path
) -> None:
    h.docs.put(1)
    await h.push(image_body([ref(1)]))
    assert (
        callable(h.svc.worker.validate.await_args.kwargs["account"])
        and len(h.mat.accounts) == 1
    )
    bare = await make_harness(tmp_path / "bare")
    try:
        await bare.push(image_body([]))
        assert "account" not in bare.svc.worker.validate.await_args.kwargs
    finally:
        await bare.store.stop()
