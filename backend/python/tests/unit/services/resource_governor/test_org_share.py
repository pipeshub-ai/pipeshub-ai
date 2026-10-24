"""One org's share of a parse pool while another org wants it (S-4)."""
from app.services.resource_governor.org_share import OrgShare

POOL = "parse_light"


def test_a_lone_org_may_use_the_whole_pool() -> None:
    share = OrgShare(0.5)
    share.arrive(POOL, "a")
    share.hold(POOL, "a", 9)
    share.arrive(POOL, "a")
    assert not share.over_share(POOL, "a", 1, limit=10)


def test_with_another_org_waiting_an_org_stops_at_its_share() -> None:
    share = OrgShare(0.5)
    for _ in range(5):
        share.arrive(POOL, "a")
        share.hold(POOL, "a", 1)
    share.arrive(POOL, "b")
    share.arrive(POOL, "a")
    assert share.over_share(POOL, "a", 1, limit=10)
    assert not share.over_share(POOL, "b", 1, limit=10)


def test_an_org_with_nothing_running_is_always_let_in() -> None:
    share = OrgShare(0.5)
    share.arrive(POOL, "b")
    share.hold(POOL, "b", 4)
    share.arrive(POOL, "a")
    assert not share.over_share(POOL, "a", 8, limit=10), "a costly request still runs"


def test_other_pools_and_finished_requests_do_not_count() -> None:
    share = OrgShare(0.5)
    share.arrive("parse_heavy", "b")
    share.arrive(POOL, "a")
    share.hold(POOL, "a", 6)
    assert not share.over_share(POOL, "a", 1, limit=10)
    share.arrive(POOL, "b")
    share.leave(POOL, "b")
    assert not share.over_share(POOL, "a", 1, limit=10)
    share.leave(POOL, "a", 6)
    assert (POOL, "a") not in share._demand and (POOL, "a") not in share._held
